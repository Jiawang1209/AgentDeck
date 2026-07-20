"""Deterministic tmux workspace for human observation of Application events."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from agentdeck.ports.runtime import (
    ObserverInstance,
    ObserverPane,
    ObserverWindow,
    ObserverWorkspacePlan,
    TakeoverOwnership,
    WORKER_OBSERVER_ROLES,
    observer_identity,
)


Runner = Callable[[tuple[str, ...]], object]
_ROLE_PANE_INDEX: Final = {
    role: index for index, role in enumerate(WORKER_OBSERVER_ROLES)
}


class TmuxObserverFailure(RuntimeError):
    ALLOWED_CODES = frozenset({
        "observer_create_failed", "observer_select_failed",
        "observer_close_failed", "observer_takeover_failed",
    })

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in self.ALLOWED_CODES:
            raise ValueError("unknown tmux Observer failure code")
        self.code = code
        super().__init__(code)


class TmuxObserver:
    """A transparency-only tmux Adapter with no domain authority."""

    def __init__(self, *, runner: Runner) -> None:
        if not callable(runner):
            raise TypeError("runner must be callable")
        self._runner = runner

    def plan(
        self, *, project_id: str, instances: tuple[ObserverInstance, ...],
    ) -> ObserverWorkspacePlan:
        project_id = observer_identity(project_id, "project_id", "prj_")
        checked = self._instances(instances)
        namespace = f"agentdeck-{project_id}"
        overview_target = f"{namespace}:Overview"
        workers_target = f"{namespace}:Workers"
        overview_command = (
            "agentdeck", "observer", "--mode", "event-subscription",
            "--read-only", "--project-id", project_id, "--view", "overview",
        )
        overview = ObserverPane(
            name=f"{namespace}-overview", target=f"{overview_target}.0",
            role="overview", instance_id=None, session_id=None, pane_id=None,
            command=overview_command,
        )
        workers = tuple(
            ObserverPane(
                name=f"{namespace}-{instance.role}",
                target=f"{workers_target}.{index}", role=instance.role,
                instance_id=instance.instance_id, session_id=instance.session_id,
                pane_id=None,
                command=(
                    "agentdeck", "observer", "--mode", "event-subscription",
                    "--read-only", "--project-id", project_id,
                    "--session-id", instance.session_id,
                    "--instance-id", instance.instance_id,
                ),
            )
            for index, instance in enumerate(checked)
        )
        return ObserverWorkspacePlan(
            project_id=project_id, socket_name=namespace, workspace_name=namespace,
            windows=(
                ObserverWindow("Overview", overview_target, (overview,)),
                ObserverWindow("Workers", workers_target, workers),
            ),
        )

    def create_workspace(
        self, *, project_id: str, instances: tuple[ObserverInstance, ...],
    ) -> ObserverWorkspacePlan:
        plan = self.plan(project_id=project_id, instances=instances)
        for argv in self._create_argv(plan):
            self._run(argv, "observer_create_failed")
        return plan

    def select_workspace(self, *, project_id: str) -> None:
        namespace = self._namespace(project_id)
        self._run(
            (*self._prefix(namespace), "select-window", "-t", f"{namespace}:Overview"),
            "observer_select_failed",
        )

    def close_workspace(self, *, project_id: str) -> None:
        namespace = self._namespace(project_id)
        self._run(
            (*self._prefix(namespace), "kill-session", "-t", namespace),
            "observer_close_failed",
        )

    def take_ownership(self, ownership: TakeoverOwnership) -> None:
        if type(ownership) is not TakeoverOwnership:
            raise TypeError("ownership must be a TakeoverOwnership")
        namespace = self._namespace(ownership.project_id)
        pane_index = _ROLE_PANE_INDEX[ownership.role]
        self._run(
            (*self._prefix(namespace), "select-pane", "-t",
             f"{namespace}:Workers.{pane_index}"),
            "observer_takeover_failed",
        )

    @staticmethod
    def _instances(
        instances: tuple[ObserverInstance, ...],
    ) -> tuple[ObserverInstance, ...]:
        if type(instances) is not tuple or any(
            type(instance) is not ObserverInstance for instance in instances
        ):
            raise TypeError("instances must be an ordered tuple of ObserverInstance values")
        roles = tuple(instance.role for instance in instances)
        if roles != WORKER_OBSERVER_ROLES:
            raise ValueError("instances must have the exact ordered worker roles")
        instance_ids = tuple(instance.instance_id for instance in instances)
        session_ids = tuple(instance.session_id for instance in instances)
        if len(set(instance_ids)) != len(instance_ids):
            raise ValueError("duplicate instance_id")
        if len(set(session_ids)) != len(session_ids):
            raise ValueError("duplicate session_id")
        return instances

    @staticmethod
    def _namespace(project_id: str) -> str:
        return f"agentdeck-{observer_identity(project_id, 'project_id', 'prj_')}"

    @staticmethod
    def _prefix(namespace: str) -> tuple[str, ...]:
        return "tmux", "-L", namespace

    @classmethod
    def _create_argv(
        cls, plan: ObserverWorkspacePlan,
    ) -> tuple[tuple[str, ...], ...]:
        prefix = cls._prefix(plan.socket_name)
        overview = plan.windows[0].panes[0]
        workers = plan.windows[1].panes
        target = f"{plan.workspace_name}:Workers"
        return (
            (*prefix, "-f", "/dev/null", "new-session", "-d", "-s",
             plan.workspace_name, "-n", "Overview", *overview.command),
            (*prefix, "new-window", "-d", "-t", f"{plan.workspace_name}:",
             "-n", "Workers", *workers[0].command),
            (*prefix, "select-pane", "-t", f"{target}.0", "-T", workers[0].name),
            (*prefix, "split-window", "-d", "-h", "-t", f"{target}.0",
             *workers[1].command),
            (*prefix, "select-pane", "-t", f"{target}.1", "-T", workers[1].name),
            (*prefix, "split-window", "-d", "-v", "-t", f"{target}.0",
             *workers[2].command),
            (*prefix, "select-pane", "-t", f"{target}.2", "-T", workers[2].name),
            (*prefix, "split-window", "-d", "-v", "-t", f"{target}.1",
             *workers[3].command),
            (*prefix, "select-pane", "-t", f"{target}.3", "-T", workers[3].name),
            (*prefix, "select-layout", "-t", target, "tiled"),
            (*prefix, "select-window", "-t", f"{plan.workspace_name}:Overview"),
        )

    def _run(self, argv: tuple[str, ...], failure_code: str) -> None:
        try:
            result = self._runner(argv)
            returncode = getattr(result, "returncode", None)
        except Exception:
            raise TmuxObserverFailure(failure_code) from None
        if type(returncode) is not int or returncode != 0:
            raise TmuxObserverFailure(failure_code)


__all__ = ["TmuxObserver", "TmuxObserverFailure"]
