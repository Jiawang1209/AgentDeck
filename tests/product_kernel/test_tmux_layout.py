from __future__ import annotations

import ast
from dataclasses import replace
import importlib
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).parents[2]
PRODUCTION_FILES = (
    ROOT / "src/agentdeck/ports/runtime.py",
    ROOT / "src/agentdeck/adapters/tmux_observer.py",
)
ROLES = ("implementer", "reviewer", "reviser", "acceptance_reviewer")


def runtime_api() -> tuple[Any, Any]:
    try:
        port = importlib.import_module("agentdeck.ports.runtime")
        adapter = importlib.import_module("agentdeck.adapters.tmux_observer")
    except ModuleNotFoundError:
        pytest.fail("Task 27 Observer Runtime modules are missing", pytrace=False)
    return port, adapter


def four_instances() -> tuple[Any, ...]:
    port, _ = runtime_api()
    return tuple(
        port.ObserverInstance(
            instance_id=f"agt_{role}",
            session_id=f"ses_{role}",
            role=role,
        )
        for role in ROLES
    )


class RecordingRunner:
    def __init__(self, *, fail_at: int | None = None, returncode: int = 0) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail_at = fail_at
        self.returncode = returncode

    def __call__(self, argv: tuple[str, ...]) -> object:
        assert type(argv) is tuple
        assert all(type(argument) is str for argument in argv)
        self.calls.append(argv)
        if self.fail_at == len(self.calls):
            raise RuntimeError("secret task output")
        return type("Result", (), {"returncode": self.returncode})()


def observer(runner: RecordingRunner | None = None) -> Any:
    _, adapter = runtime_api()
    return adapter.TmuxObserver(runner=runner or RecordingRunner())


def test_plan_has_exact_workspace_windows_roles_and_observer_argv() -> None:
    plan = observer().plan(project_id="prj_1", instances=four_instances())

    assert [window.name for window in plan.windows] == ["Overview", "Workers"]
    workers = plan.windows[1]
    assert [pane.role for pane in workers.panes] == list(ROLES)
    assert all(pane.command[:2] == ("agentdeck", "observer") for pane in workers.panes)
    assert all(type(pane.command) is tuple for pane in workers.panes)
    assert all("event-subscription" in pane.command for pane in workers.panes)
    assert all("--read-only" in pane.command for pane in workers.panes)


def test_overview_and_worker_commands_are_content_free_read_only_subscriptions() -> None:
    plan = observer().plan(project_id="prj_1", instances=four_instances())
    panes = tuple(pane for window in plan.windows for pane in window.panes)

    assert plan.windows[0].panes[0].command == (
        "agentdeck", "observer", "--mode", "event-subscription", "--read-only",
        "--project-id", "prj_1", "--view", "overview",
    )
    for pane, role in zip(plan.windows[1].panes, ROLES, strict=True):
        assert pane.command == (
            "agentdeck", "observer", "--mode", "event-subscription", "--read-only",
            "--project-id", "prj_1", "--session-id", f"ses_{role}",
            "--instance-id", f"agt_{role}",
        )
    forbidden = ("task", "prompt", "secret", "database", "sqlite", "raw-output", "pty")
    assert not any(marker in " ".join(pane.command).lower() for pane in panes for marker in forbidden)


def test_observer_surface_has_no_dispatch_completion_or_database_authority() -> None:
    port, adapter = runtime_api()
    forbidden = {
        "send_task", "dispatch", "mark_completed", "infer_completion",
        "write_database", "write_state", "save_result",
    }

    assert forbidden.isdisjoint(port.ObserverRuntime.__dict__)
    assert forbidden.isdisjoint(adapter.TmuxObserver.__dict__)
    assert {
        "create_workspace", "select_workspace", "close_workspace",
        "take_ownership",
    }.issubset(port.ObserverRuntime.__dict__)
    assert "return_ownership" not in port.ObserverRuntime.__dict__
    assert "return_ownership" not in adapter.TmuxObserver.__dict__


@pytest.mark.parametrize(
    "project_id",
    ("", "project", "prj_../escape", "prj_a:b", "prj_a/b", "prj_a b", 1, None),
)
def test_project_unsafe_ids_fail_before_runner(project_id: object) -> None:
    runner = RecordingRunner()

    with pytest.raises((TypeError, ValueError)):
        observer(runner).create_workspace(
            project_id=project_id, instances=four_instances()  # type: ignore[arg-type]
        )

    assert runner.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("instance_id", "agt_a:b"), ("instance_id", "agt_../x"),
        ("instance_id", 1), ("session_id", "ses_a/b"),
        ("session_id", "ses_a b"), ("session_id", None),
    ),
)
def test_instance_identities_are_plain_and_project_safe(field: str, value: object) -> None:
    port, _ = runtime_api()

    with pytest.raises((TypeError, ValueError)):
        port.ObserverInstance(**{
            "instance_id": "agt_safe", "session_id": "ses_safe",
            "role": "implementer", field: value,
        })


@pytest.mark.parametrize("duplicate_field", ("instance_id", "session_id"))
def test_duplicate_instance_or_session_ids_fail_before_runner(duplicate_field: str) -> None:
    instances = list(four_instances())
    instances[1] = replace(
        instances[1], **{duplicate_field: getattr(instances[0], duplicate_field)}
    )
    runner = RecordingRunner()

    with pytest.raises(ValueError, match=f"duplicate {duplicate_field}"):
        observer(runner).create_workspace(project_id="prj_1", instances=tuple(instances))

    assert runner.calls == []


@pytest.mark.parametrize("variant", ("missing", "extra", "wrong", "unstable"))
def test_worker_role_shape_and_input_order_fail_closed(variant: str) -> None:
    port, _ = runtime_api()
    instances = list(four_instances())
    if variant == "missing":
        instances.pop()
    elif variant == "extra":
        instances.append(port.ObserverInstance("agt_leader", "ses_leader", "leader"))
    elif variant == "wrong":
        instances[-1] = replace(instances[-1], role="reviewer")
    else:
        instances.reverse()
    runner = RecordingRunner()

    with pytest.raises(ValueError, match="ordered worker roles"):
        observer(runner).create_workspace(project_id="prj_1", instances=tuple(instances))

    assert runner.calls == []


def test_names_and_targets_are_deterministic_and_project_namespaced() -> None:
    first = observer().plan(project_id="prj_1", instances=four_instances())
    repeated = observer().plan(project_id="prj_1", instances=four_instances())
    other = observer().plan(project_id="prj_2", instances=four_instances())

    assert first == repeated
    assert first.workspace_name == first.socket_name == "agentdeck-prj_1"
    assert first.workspace_name != other.workspace_name
    assert {window.target for window in first.windows}.isdisjoint(
        {window.target for window in other.windows}
    )
    assert {pane.target for window in first.windows for pane in window.panes}.isdisjoint(
        {pane.target for window in other.windows for pane in window.panes}
    )
    assert [pane.name for pane in first.windows[1].panes] == [
        f"agentdeck-prj_1-{role}" for role in ROLES
    ]
    assert all(pane.pane_id is None for pane in first.windows[1].panes)


def expected_create_calls(plan: Any) -> list[tuple[str, ...]]:
    prefix = ("tmux", "-L", plan.socket_name)
    overview = plan.windows[0].panes[0]
    workers = plan.windows[1].panes
    target = f"{plan.workspace_name}:Workers"
    return [
        (*prefix, "-f", "/dev/null", "new-session", "-d", "-s", plan.workspace_name,
         "-n", "Overview", *overview.command),
        (*prefix, "new-window", "-d", "-t", f"{plan.workspace_name}:", "-n", "Workers",
         *workers[0].command),
        (*prefix, "select-pane", "-t", f"{target}.0", "-T", workers[0].name),
        (*prefix, "split-window", "-d", "-h", "-t", f"{target}.0", *workers[1].command),
        (*prefix, "select-pane", "-t", f"{target}.1", "-T", workers[1].name),
        (*prefix, "split-window", "-d", "-v", "-t", f"{target}.1", *workers[2].command),
        (*prefix, "select-pane", "-t", f"{target}.2", "-T", workers[2].name),
        (*prefix, "split-window", "-d", "-v", "-t", f"{target}.2", *workers[3].command),
        (*prefix, "select-pane", "-t", f"{target}.3", "-T", workers[3].name),
        (*prefix, "select-layout", "-t", target, "tiled"),
        (*prefix, "select-window", "-t", f"{plan.workspace_name}:Overview"),
    ]


def test_split_insertion_order_keeps_takeover_bound_to_exact_worker() -> None:
    port, _ = runtime_api()
    runner = RecordingRunner()
    adapter = observer(runner)
    instances = four_instances()
    by_instance_id = {
        instance.instance_id: (
            instance.role, instance.session_id, instance.instance_id,
        )
        for instance in instances
    }

    adapter.create_workspace(project_id="prj_1", instances=instances)
    pane_bindings: list[tuple[str, str, str]] = []
    for argv in runner.calls:
        if "new-window" not in argv and "split-window" not in argv:
            continue
        instance_id = argv[argv.index("--instance-id") + 1]
        binding = by_instance_id[instance_id]
        if "new-window" in argv:
            pane_bindings.append(binding)
        else:
            target = argv[argv.index("-t") + 1]
            target_index = int(target.rsplit(".", 1)[1])
            pane_bindings.insert(target_index + 1, binding)

    selected_bindings = []
    for instance in instances:
        adapter.take_ownership(port.TakeoverOwnership(
            project_id="prj_1", instance_id=instance.instance_id,
            session_id=instance.session_id, role=instance.role, owner_id="human",
        ))
        target = runner.calls[-1][runner.calls[-1].index("-t") + 1]
        selected_bindings.append(pane_bindings[int(target.rsplit(".", 1)[1])])

    expected = tuple(by_instance_id[instance.instance_id] for instance in instances)
    assert (tuple(pane_bindings), tuple(selected_bindings)) == (expected, expected)


def test_create_workspace_uses_exact_injected_tmux_argv() -> None:
    runner = RecordingRunner()
    adapter = observer(runner)
    plan = adapter.create_workspace(project_id="prj_1", instances=four_instances())

    assert runner.calls == expected_create_calls(plan)


def test_select_close_and_takeover_use_exact_injected_tmux_argv() -> None:
    port, _ = runtime_api()
    runner = RecordingRunner()
    adapter = observer(runner)
    ownership = port.TakeoverOwnership(
        project_id="prj_1", instance_id="agt_reviewer",
        session_id="ses_reviewer", role="reviewer", owner_id="human",
    )

    adapter.select_workspace(project_id="prj_1")
    adapter.take_ownership(ownership)
    adapter.close_workspace(project_id="prj_1")

    prefix = ("tmux", "-L", "agentdeck-prj_1")
    assert runner.calls == [
        (*prefix, "select-window", "-t", "agentdeck-prj_1:Overview"),
        (*prefix, "select-pane", "-t", "agentdeck-prj_1:Workers.1"),
        (*prefix, "kill-session", "-t", "agentdeck-prj_1"),
    ]


@pytest.mark.parametrize("mode", ("raise", "nonzero"))
def test_runner_failure_is_content_free_adapter_diagnostic(mode: str) -> None:
    port, adapter_module = runtime_api()
    runner = RecordingRunner(fail_at=1 if mode == "raise" else None,
                             returncode=0 if mode == "raise" else 7)
    adapter = observer(runner)

    with pytest.raises(adapter_module.TmuxObserverFailure) as caught:
        adapter.select_workspace(project_id="prj_1")

    assert caught.value.code == "observer_select_failed"
    assert "secret" not in str(caught.value).lower()
    assert not hasattr(adapter, "store")
    assert not hasattr(adapter, "database")
    assert port.ObserverRuntime is not None


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_runtime_port_and_tmux_adapter_preserve_architecture_firewalls() -> None:
    runtime_api()
    port_imports = imported_modules(PRODUCTION_FILES[0])
    adapter_imports = imported_modules(PRODUCTION_FILES[1])

    assert not any(name.startswith("agentdeck.adapters") for name in port_imports)
    assert not any(name.startswith("agentdeck.runtime") for name in adapter_imports)
    assert not any(
        marker in name.lower()
        for name in port_imports | adapter_imports
        for marker in ("agentdeck.state", "agentdeck.models", "sqlite", "store")
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in PRODUCTION_FILES)
    assert "shell=True" not in source
    assert "runtime.tmux" not in source
