from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from hashlib import sha256
import json
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
from types import MappingProxyType
from uuid import uuid4

from agentdeck.adapters.config import ConfigResolver
from agentdeck.adapters.acp import ACPWorker
from agentdeck.adapters.acp_leader import ACPLeader
from agentdeck.adapters.acp_transport import ACPStdioTransport
from agentdeck.adapters.acp_worker_connection import create_worker_connection
from agentdeck.adapters.adapter_readiness import (
    AdapterReadiness, blocked_readiness, canonical_project_root,
    execution_command, verified_readiness,
)
from agentdeck.adapters.codex_app_server_probe import probe_codex_bridge
from agentdeck.adapters.discovery import (
    ClaudeAdapterFacts, CodexAdapterFacts, ReadinessState, ToolDiscovery,
    classify_claude, classify_codex, discover_tools,
)
from agentdeck.adapters.observer_ipc import UnixObserverServer
from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.adapters.project_evidence import GitProjectEvidenceSource
from agentdeck.adapters.system_clock import SystemClock
from agentdeck.application.approval_service import ApprovalService
from agentdeck.application.async_exit_coordinator import AsyncExitCoordinator
from agentdeck.application.execution_runtime import ForegroundExecutionRuntime
from agentdeck.application.execution_service import ExecutionService
from agentdeck.application.observer_broker import ObserverBroker
from agentdeck.application.takeover_control import TakeoverControl
from agentdeck.application.exit_service import ExitService
from agentdeck.application.golden_acceptance import assemble_golden_report
from agentdeck.application.leader_service import LeaderService
from agentdeck.application.mission_service import MissionService
from agentdeck.application.project_lifecycle_service import ProjectLifecycleService
from agentdeck.application.recovery_service import RecoveryService
from agentdeck.application.preflight_service import (
    EnvironmentReport, PreflightService,
)
from agentdeck.application.session_service import SessionService
from agentdeck.ports.clock import Clock
from agentdeck.ports.leader import (
    AvailableAgent, LeaderRequest, ProjectContext, ResolvedLeaderModel,
)
from agentdeck.kernel.agents import AgentBackend, AgentInstance, AgentRole
from agentdeck.kernel.execution import EvidenceKind
from agentdeck.kernel.mission import MissionDraft
from agentdeck.kernel.permissions import PermissionProfile, PermissionScope
from agentdeck.ports.observer import ObserverCursor
from agentdeck.ports.transport import TransportPort
from agentdeck.product.renderer import render
from agentdeck.product.observer_lifecycle import ProductObserverLifecycle
from agentdeck.product.shell import (
    AsyncTerminalReader, ProductShell, validate_mission_preview,
)


def _new_session_id() -> str:
    return f"ses_{uuid4().hex}"


def _new_exit_request_id() -> str:
    return f"xrt_{uuid4().hex}"


def _new_recovery_run_id() -> str:
    return f"restart_{uuid4().hex}"


WorkerAgentFactory = Callable[
    [tuple[str, ...], str, tuple[tuple[str, str], ...]], object
]
TransportFactory = Callable[..., TransportPort]


class _ActivePermissionSnapshotSource:
    def __init__(self) -> None:
        self._control: TakeoverControl | None = None

    def bind(self, control: TakeoverControl) -> None:
        if type(control) is not TakeoverControl or self._control is not None:
            raise ValueError("permission proof source binding is invalid")
        self._control = control

    def __call__(self) -> PermissionScope:
        active = None if self._control is None else self._control._active
        if active is None or type(active.permission) is not PermissionScope:
            raise RuntimeError("permission snapshot is unavailable")
        return active.permission


class _RuntimeObserverCursorSource:
    def __init__(self, *, runtime: object, lifecycle: ProductObserverLifecycle) -> None:
        if not callable(getattr(runtime, "status", None)):
            raise TypeError("Observer cursor source requires runtime status")
        self._runtime, self._lifecycle = runtime, lifecycle

    def __call__(self) -> ObserverCursor | None:
        status = self._runtime.status()
        attempt_id = status.attempt_id if status.state == "active" else None
        return None if attempt_id is None else self._lifecycle.current_cursor(attempt_id)


@dataclass(frozen=True)
class ACPAdapterComposition:
    """Lazy per-instance ACP composition; readiness itself performs no I/O."""

    readiness: Mapping[str, AdapterReadiness]
    project_root: str
    clock: Clock
    worker_agent_factory: WorkerAgentFactory
    transport_factory: TransportFactory
    _worker_owners: list[object] = field(
        default_factory=list, repr=False, compare=False,
    )

    def _require_ready(self, backend_id: str) -> AdapterReadiness:
        if type(backend_id) is not str:
            raise ValueError("ACP adapter backend is not ready")
        value = self.readiness.get(backend_id)
        if not verified_readiness(value, backend_id):
            raise ValueError(f"ACP adapter {backend_id} is not ready")
        return value

    def leader(self, backend_id: str, *, model: str) -> ACPLeader:
        ready = self._require_ready(backend_id)
        command = execution_command(ready, model=model)
        transport_factory = partial(
            self.transport_factory, environment=dict(ready.environment),
        )
        return ACPLeader(
            command, backend_id=backend_id, model=model,
            version=ready.version, transport_factory=transport_factory,
        )

    def worker(self, backend_id: str, *, model: str = "native-default") -> ACPWorker:
        ready = self._require_ready(backend_id)
        command = execution_command(ready, model=model)
        agent = self.worker_agent_factory(
            command, self.project_root, ready.environment,
        )
        if any(owner is agent for owner in self._worker_owners):
            raise ValueError("ACP Worker factory must return a fresh connection owner")
        self._worker_owners.append(agent)
        return ACPWorker(
            agent=agent, project_root=self.project_root, clock=self.clock,
            project_boundary_enforced=True,
        )


def build_acp_adapter_composition(
    *, readiness: Mapping[str, AdapterReadiness], project_root: str,
    clock: Clock, worker_agent_factory: WorkerAgentFactory | None = None,
    transport_factory: TransportFactory = ACPStdioTransport,
) -> ACPAdapterComposition:
    project_root = canonical_project_root(project_root)
    if not callable(getattr(clock, "now", None)):
        raise TypeError("clock must expose now")
    selected_worker_factory = (
        create_worker_connection
        if worker_agent_factory is None else worker_agent_factory
    )
    if not callable(selected_worker_factory) or not callable(transport_factory):
        raise TypeError("ACP composition factories must be callable")
    copied: dict[str, AdapterReadiness] = {}
    for key, value in readiness.items():
        if type(key) is not str or type(value) is not AdapterReadiness:
            raise TypeError("ACP readiness mapping is invalid")
        copied[key] = value
    return ACPAdapterComposition(
        MappingProxyType(copied), project_root, clock,
        selected_worker_factory, transport_factory,
    )


def build_product_shell(
    *,
    project_root: str,
    read_line: Callable | None = None,
    write_line: Callable[[str], object] = print,
    clock_factory: Callable[[], object] = SystemClock,
    discovery_factory: Callable[[], Mapping[str, ToolDiscovery]] = discover_tools,
    config_factory: Callable[..., object] = ConfigResolver,
    store_factory: Callable[..., object] = SQLiteStore.open,
    shell_factory: Callable[..., ProductShell] = ProductShell,
    mission_service_factory: Callable[..., object] | None = None,
    session_id_factory: Callable[[], str] = _new_session_id,
    exit_request_id_factory: Callable[[], str] = _new_exit_request_id,
    recovery_run_id_factory: Callable[[], str] = _new_recovery_run_id,
    runtime_factory: Callable[[], object] = ForegroundExecutionRuntime,
    lifecycle_factory: Callable[..., object] = ProjectLifecycleService,
    recovery_factory: Callable[..., object] = RecoveryService,
    approval_service_factory: Callable[..., object] = ApprovalService,
    execution_service_factory: Callable[..., object] = ExecutionService,
    exit_coordinator_factory: Callable[..., object] = AsyncExitCoordinator,
    adapter_composition_factory: Callable[..., object] = build_acp_adapter_composition,
    adapter_readiness: Mapping[str, AdapterReadiness] | None = None,
) -> ProductShell:
    """Compose the foreground Product Shell through injectable factories."""

    clock = clock_factory()
    discovered = discovery_factory()
    config = config_factory(
        discovered={"permission": "approve-for-me"},
        global_values={},
        project_values={},
        session_values={},
    )
    default_permission = config.resolve("permission").value
    available_leaders = _available_leaders(discovered)
    store = store_factory(project_root, clock=clock)
    try:
        service = SessionService.open_latest(
            store=store,
            clock=clock,
            project_root=project_root,
            available_leaders=available_leaders,
            session_id_factory=session_id_factory,
        )
        session_id = service.current().session_id
        runtime = runtime_factory()
        if not callable(getattr(runtime, "is_empty", None)) or not runtime.is_empty():
            raise RuntimeError("fresh Product composition requires an empty runtime")
        lifecycle = lifecycle_factory(
            store=store, clock=clock, session_id=session_id,
        )
        observer_lifecycle = ProductObserverLifecycle()
        observer_server = UnixObserverServer(
            project_root=Path(project_root), project_id=store._project_id,
            acknowledge=observer_lifecycle.acknowledge,
            cursor_reader=observer_lifecycle.read_cursor,
        )
        observer_broker = ObserverBroker(
            project_id=store._project_id, store=store, clock=clock,
            channel=observer_server,
        )
        observer_lifecycle.bind(server=observer_server, publisher=observer_broker)
        recovery = recovery_factory(
            store=store, clock=clock, session_id=session_id,
            recovery_run_id=recovery_run_id_factory(),
        )
        exit_service = ExitService(
            store=store,
            clock=clock,
            session_id=session_id,
            request_id_factory=exit_request_id_factory,
        )
        mission_service = None
        if mission_service_factory is not None:
            mission_service = mission_service_factory(
                store=store, clock=clock, session_service=service,
                available_leaders=available_leaders, project_root=project_root,
                preview_validator=validate_mission_preview,
            )
        execution = None
        if adapter_readiness is not None:
            adapters = adapter_composition_factory(
                readiness=adapter_readiness, project_root=project_root,
                clock=clock,
            )
            approval = approval_service_factory(
                store=store, clock=clock,
                event_publisher=observer_lifecycle.publisher,
            )
            execution = execution_service_factory(
                store=store, clock=clock, approval_service=approval,
                worker_factory=lambda task: adapters.worker(task.backend),
                runtime=runtime, lifecycle=lifecycle,
            )
            if type(execution) is ExecutionService:
                permission_source = _ActivePermissionSnapshotSource()
                control = TakeoverControl(
                    store=store, clock=clock, runtime=runtime,
                    project_evidence=GitProjectEvidenceSource(
                        project_root=Path(project_root), project_id=store._project_id,
                    ).capture,
                    permission_snapshot=permission_source,
                    observer_cursor=_RuntimeObserverCursorSource(
                        runtime=runtime, lifecycle=observer_lifecycle,
                    ),
                )
                permission_source.bind(control)
                execution.configure_takeover_control(control)
        exit_coordinator = exit_coordinator_factory(
            exit_service=exit_service, store=store, clock=clock,
            runtime=runtime, lifecycle=lifecycle, session_id=session_id,
        )
        reader = (
            AsyncTerminalReader(sys.stdin, sys.stdout)
            if read_line is None else read_line
        )
        return shell_factory(
            session_service=service,
            exit_coordinator=exit_coordinator,
            recovery_service=recovery,
            lifecycle=lifecycle,
            mission_service=mission_service,
            execution_service=execution,
            observer_lifecycle=observer_lifecycle,
            resume_snapshot_loader=lambda: store.load_execution_resume(session_id),
            available_leaders=available_leaders,
            read_line=reader,
            write_line=write_line,
            close=store.close,
            default_permission=default_permission,
            render_text=render,
        )
    except BaseException:
        store.close()
        raise


def _available_leaders(
    discovered: Mapping[str, ToolDiscovery],
) -> dict[str, tuple[str, ...]]:
    available: dict[str, tuple[str, ...]] = {}
    for name, fact in discovered.items():
        if (
            type(name) is str
            and type(fact) is ToolDiscovery
            and fact.readiness is ReadinessState.READY
            and "leader" in fact.capabilities
            and name in {"codex", "claude"}
        ):
            available[f"{name}-cli"] = ("native-default",)
    return dict(sorted(available.items()))


def run_product_dev(*, diagnostic: bool = False) -> int:
    if diagnostic:
        print("AgentDeck Product Kernel development entry: ready")
        return 0
    shell = build_product_shell(project_root=str(Path.cwd()))
    return asyncio.run(shell.run_async())


# --- Read-only real preflight (Task 35) ------------------------------------


def _tool_summary(fact: ToolDiscovery | None) -> str:
    if fact is None or fact.resolved_path is None:
        return "missing"
    return f"{fact.resolved_path}@{fact.version or 'unknown'}"


def _acp_summary(readiness: AdapterReadiness) -> str:
    return "acp_available" if readiness.ready else "unavailable"


def _readiness_summary(readiness: AdapterReadiness) -> str:
    if readiness.ready:
        return f"{readiness.cli_path}@{readiness.cli_version}"
    code = readiness.diagnostic.code if readiness.diagnostic else "not_ready"
    return f"not_ready:{code}"


def _cli_semver(path: str | None, argument: str = "--version") -> str | None:
    if path is None:
        return None
    try:
        completed = subprocess.run(
            [path, argument], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\d+\.\d+(?:\.\d+)?", completed.stdout or completed.stderr or "")
    return match.group(0) if match else None


def _real_codex_readiness() -> AdapterReadiness:
    """Classify real Codex ACP facts via the bounded, read-only bridge probe."""
    codex = shutil.which("codex")
    bridge = shutil.which("agentdeck-codex-acp")
    if codex is None or bridge is None:
        return classify_codex(
            CodexAdapterFacts(
                cli_path=codex, cli_version=None, app_server_available=False,
                app_server_version=None, bridge_path=bridge, schema_digest=None,
            )
        )
    try:
        probe = probe_codex_bridge([codex, "app-server"])
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
        return blocked_readiness("codex-cli", "codex_app_server_probe_failed")
    version = probe.version or ""
    app_server_version = version.removeprefix("codex-cli ").strip() or None
    return classify_codex(
        CodexAdapterFacts(
            cli_path=codex, cli_version=probe.version,
            app_server_available=probe.ready,
            app_server_version=app_server_version, bridge_path=bridge,
            schema_digest=probe.schema_digest,
        )
    )


def _claude_authenticated(claude: str | None) -> bool:
    """Passive login check via ``claude auth status`` (no prompt, read-only)."""
    if claude is None:
        return False
    try:
        completed = subprocess.run(
            [claude, "auth", "status"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return False
    text = completed.stdout.strip()
    if not text.startswith("{"):
        return False
    try:
        return json.loads(text).get("loggedIn") is True
    except json.JSONDecodeError:
        return False


def _real_claude_readiness() -> AdapterReadiness:
    """Classify real Claude ACP facts from passive CLI/auth/adapter checks."""
    claude = shutil.which("claude")
    adapter = shutil.which("claude-agent-acp")
    cli_semver = _cli_semver(claude)
    adapter_semver = _cli_semver(adapter)
    return classify_claude(
        ClaudeAdapterFacts(
            cli_path=claude,
            cli_version=f"claude-cli {cli_semver}" if cli_semver else None,
            authenticated=_claude_authenticated(claude),
            adapter_path=adapter,
            adapter_version=(
                f"claude-agent-acp {adapter_semver}" if adapter_semver else None
            ),
        )
    )


class RealPreflightProbe:
    """Read-only environment probe for the authorized real preflight.

    ACP readiness comes from the real adapter classifiers (``classify_codex`` /
    ``classify_claude`` over facts gathered by the bounded ``probe_codex_bridge``
    and passive CLI/auth checks) — never from bare ``discover_tools`` PATH
    lookup, which cannot observe ACP availability without passive probes. It
    installs nothing, authenticates nothing, selects no fallback, generates no
    source, and sends no model prompt. Discovery and the two readiness sources
    are injectable so the probe is deterministically testable.
    """

    def __init__(
        self,
        project_root: str,
        *,
        discovery: Callable[[], Mapping[str, ToolDiscovery]] = discover_tools,
        codex_readiness: Callable[[], AdapterReadiness] = _real_codex_readiness,
        claude_readiness: Callable[[], AdapterReadiness] = _real_claude_readiness,
    ) -> None:
        self._project_root = Path(project_root)
        self._discovery = discovery
        self._codex_readiness = codex_readiness
        self._claude_readiness = claude_readiness

    def inspect(self) -> EnvironmentReport:
        discovered = self._discovery()
        tmux = discovered.get("tmux")
        codex = self._codex_readiness()
        claude = self._claude_readiness()
        facts = {
            "python_version": platform.python_version(),
            "python_executable": sys.executable,
            "codex_cli": _readiness_summary(codex),
            "claude_cli": _readiness_summary(claude),
            "codex_acp": _acp_summary(codex),
            "claude_acp": _acp_summary(claude),
            "codex_app_server_schema": codex.schema_digest or "unavailable",
            "tmux": _tool_summary(tmux),
            "sqlite": self._sqlite_summary(),
        }
        blockers: list[str] = []
        if not codex.ready:
            blockers.append("codex_not_ready")
        if not claude.ready:
            blockers.append("claude_not_ready")
        if tmux is None or tmux.resolved_path is None:
            blockers.append("tmux_unavailable")
        if facts["sqlite"] == "corrupt":
            blockers.append("sqlite_corrupt")
        return EnvironmentReport(facts=facts, blockers=tuple(blockers))

    def _sqlite_summary(self) -> str:
        database = self._project_root / ".agentdeck" / "state.db"
        if not database.exists():
            return "absent"
        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            try:
                row = connection.execute("PRAGMA integrity_check").fetchone()
            finally:
                connection.close()
        except sqlite3.Error:
            return "corrupt"
        return "ok" if row and row[0] == "ok" else "corrupt"


_PERMISSION_BY_FLAG = {
    "ask-for-approval": PermissionProfile.ASK_FOR_APPROVAL,
    "approve-for-me": PermissionProfile.APPROVE_FOR_ME,
    "full-access": PermissionProfile.FULL_ACCESS,
}


def run_product_preflight(
    *,
    real: bool,
    commit: str,
    leader: str,
    model: str,
    permission: str,
    authority_digest: str,
    target_manifest: str,
    as_json: bool,
    project_root: str | None = None,
) -> int:
    """Run the read-only real preflight; return 0 only when ready.

    Authorization is never inferred: this command records redacted facts and a
    verdict, but starting a real Golden Mission remains a separate, explicitly
    authorized step.
    """
    if not real:
        print(
            "Refusing to run: pass --real to execute the authorized read-only "
            "preflight."
        )
        return 2
    profile = _PERMISSION_BY_FLAG.get(permission)
    if profile is None:
        print(f"Unknown permission profile: {permission!r}")
        return 2
    root = Path(project_root) if project_root else Path.cwd()
    target_manifest_hash = ""
    if target_manifest:
        manifest_path = Path(target_manifest)
        if not manifest_path.is_file():
            print(f"target manifest not found: {target_manifest}")
            return 2
        target_manifest_hash = "sha256:" + sha256(
            manifest_path.read_bytes()
        ).hexdigest()
    leader_model = f"{leader}/{model}" if leader and model else (model or leader)

    service = PreflightService(
        project_root=root,
        probe=RealPreflightProbe(str(root)),
        clock=SystemClock(),
    )
    result = service.run(
        commit=commit,
        leader_model=leader_model,
        authority_digest=authority_digest,
        target_manifest_hash=target_manifest_hash,
        permission_profile=profile,
    )
    payload = {
        "ready": result.ready,
        "blockers": list(result.blockers),
        "facts": {key: result.facts[key] for key in sorted(result.facts)},
        "evidence_path": result.evidence_path,
    }
    if as_json:
        print(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2))
    else:
        print(f"ready={result.ready}")
        print("blockers: " + (", ".join(result.blockers) or "(none)"))
        for key in sorted(result.facts):
            print(f"  {key}: {result.facts[key]}")
    return 0 if result.ready else 1


# --- Golden-run orchestration (driver) -------------------------------------


class _NullEventPublisher:
    def publish(self, _event: object) -> None:
        return None


@dataclass(frozen=True)
class GoldenRunResult:
    status: str
    started_roles: tuple[str, ...]
    acceptance: str
    handoff_count: int
    evidence_criteria: frozenset[str]
    mission_id: str
    worker_backends: tuple[str, ...]
    agent_instance_ids: tuple[str, ...]
    acp_session_ids: tuple[str, ...]
    sqlite_integrity: str
    execution_result: object


class GoldenRunner:
    """Drive one goal through the real four-stage Mission via injectable Ports.

    Composes the same real Application graph ``build_product_shell`` composes
    (real ``SessionService`` / ``MissionService`` / ``ExecutionService`` /
    ``ApprovalService`` / ``ProjectLifecycleService`` / ``SQLiteStore``) and
    drives say -> configure -> preview -> confirm -> a completed four-stage
    Mission. The Leader and the per-stage Worker factory are injected, so the
    same driver runs with fake ACP boundaries (deterministic tests) or the real
    ACP adapters (the live Golden run). It advances all stages through
    ``ExecutionService.run_confirmed_mission``; no human script advances stages.
    """

    def __init__(
        self,
        *,
        project_root: Path,
        leader: object,
        worker_factory: Callable[[object], object],
        available_leaders: Mapping[str, tuple[str, ...]],
        clock: object | None = None,
        session_id: str = "ses_golden_run",
        event_publisher: object | None = None,
    ) -> None:
        self._project_root = Path(project_root)
        self._leader = leader
        self._worker_factory = worker_factory
        self._available_leaders = dict(available_leaders)
        self._clock = clock if clock is not None else SystemClock()
        self._session_id = session_id
        self._event_publisher = (
            event_publisher if event_publisher is not None else _NullEventPublisher()
        )

    def _seed_agent_instances(self, store, tasks) -> None:
        # Register the four distinct Agent Instances the confirmed Mission's
        # Tasks name before any Attempt starts, through the domain-typed store
        # provisioning path (no raw SQL). ACP session binding follows at dispatch.
        instances = tuple(
            AgentInstance(
                instance_id=task.agent_instance_id,
                backend=AgentBackend(
                    backend_id=task.backend, transport="acp", version="1",
                ),
                role=AgentRole(task.role.value),
                session_id=self._session_id,
            )
            for task in tasks
        )
        store.provision_agent_instances(
            instances=instances, state="active",
            now=self._clock.now().isoformat(),
        )

    async def run(
        self,
        *,
        goal: str,
        leader_backend: str,
        leader_model: str,
        permission_profile: PermissionProfile,
    ) -> GoldenRunResult:
        store = SQLiteStore.open(self._project_root, clock=self._clock)
        try:
            session = SessionService(
                store=store, clock=self._clock, session_id=self._session_id,
                project_root=str(self._project_root),
                available_leaders=self._available_leaders,
            )
            probe = MissionDraft.coding_default(
                "drf_probe", "probe objective", str(self._project_root),
                leader_backend, leader_model, permission_profile,
            )
            self._seed_agent_instances(store, probe.tasks)
            runtime = ForegroundExecutionRuntime()
            lifecycle = ProjectLifecycleService(
                store=store, clock=self._clock, session_id=self._session_id,
            )
            approval = ApprovalService(
                store=store, clock=self._clock,
                event_publisher=self._event_publisher,
            )
            execution = ExecutionService(
                store=store, clock=self._clock, approval_service=approval,
                worker_factory=self._worker_factory, runtime=runtime,
                lifecycle=lifecycle,
            )
            available_agents = tuple(
                AvailableAgent(
                    instance_id=task.agent_instance_id, role=task.role,
                    backend_id=task.backend, acp_route_id=task.acp_route,
                )
                for task in probe.tasks
            )
            request_template = LeaderRequest(
                user_goal="placeholder goal",
                project_context=ProjectContext(
                    project_root=str(self._project_root),
                    summary="golden product mission",
                ),
                available_agents=available_agents,
                permission_ceiling=permission_profile,
                resolved_model=ResolvedLeaderModel(
                    backend_id=leader_backend, adapter_id="acp",
                    model_id=leader_model, version="unreported",
                ),
            )
            if not session.accept_text(goal).accepted:
                raise RuntimeError("golden goal was not accepted")
            configured = session.configure(
                leader=leader_backend, model=leader_model,
                permission=permission_profile.value,
            )
            if not configured.accepted:
                raise RuntimeError("golden configure was rejected")
            # MissionService validates the Leader/model/permission setup
            # authority at construction, so it is built only after configure().
            mission = MissionService(
                store=store, clock=self._clock, session_id=self._session_id,
                leader_service=LeaderService(self._leader),
                request_template=request_template, session_authority=session,
                preview_validator=validate_mission_preview,
            )
            resumed = session.resume()
            if resumed.goal is not None:
                proposal = mission.propose(resumed.goal)
                if proposal.preview is None:
                    raise RuntimeError("golden mission proposal failed")
            preview = mission.current_preview()
            if preview is None:
                raise RuntimeError("no golden Mission Preview is available")
            draft = preview.draft
            confirmation = mission.confirm(preview.preview_id, preview.content_hash)
            if confirmation.mission is None:
                raise RuntimeError("golden mission confirmation failed")
            confirmed = confirmation.mission
            execution_result = await execution.run_confirmed_mission(
                session_id=session.current().session_id, confirmed=confirmed,
                draft=draft,
                permission_scope=PermissionScope.for_profile(draft.permission_profile),
            )
            if execution_result.diagnostic is None:
                lifecycle.complete_mission()

            role_by_task = {task.task_id: task.role.value for task in draft.tasks}
            backend_by_task = {task.task_id: task.backend for task in draft.tasks}
            started_roles = tuple(
                role_by_task[attempt.task_id]
                for attempt in execution_result.attempts
            )
            worker_backends = tuple(
                backend_by_task[attempt.task_id]
                for attempt in execution_result.attempts
            )
            agent_instance_ids = tuple(
                store.load_aggregate("attempts", attempt.attempt_id)[
                    "agent_instance_id"
                ]
                for attempt in execution_result.attempts
            )
            acp_session_ids = tuple(
                store.load_aggregate("attempts", attempt.attempt_id)["acp_session_id"]
                for attempt in execution_result.attempts
            )
            acceptance_evidence = next(
                (
                    item for item in execution_result.evidence
                    if item.kind is EvidenceKind.ACCEPTANCE_RESULT
                ),
                None,
            )
            evidence_criteria: frozenset[str] = frozenset()
            if acceptance_evidence is not None:
                payload = json.loads(acceptance_evidence.canonical_content)
                evidence_criteria = frozenset(payload["evidence_by_criterion"])
            passed = execution_result.diagnostic is None
            return GoldenRunResult(
                status="completed" if passed else "failed",
                started_roles=started_roles,
                acceptance="passed" if passed else "failed",
                handoff_count=len(execution_result.handoffs),
                evidence_criteria=evidence_criteria,
                mission_id=confirmed.mission_id,
                worker_backends=worker_backends,
                agent_instance_ids=agent_instance_ids,
                acp_session_ids=acp_session_ids,
                sqlite_integrity=store.integrity_check(),
                execution_result=execution_result,
            )
        finally:
            store.close()


def build_golden_report(
    *,
    run_result: GoldenRunResult,
    browser_report: object,
    frozen_commit: str,
    authority_digest: str,
    leader_backend: str,
    build_evidence: object,
    test_evidence: object,
    findings_resolution: object,
    permission_lineage: object,
    tmux_fidelity: object,
    diagnostics: object,
    exit_reentry: object,
    final_result: str,
    human_acceptance: object,
) -> dict:
    """Fuse a `GoldenRunResult` and a Task 34 browser report into a validated
    acceptance report. Composition-root glue: it reads the run's real ACP
    identity and integrity and the browser evidence's per-viewport hashes,
    interactions, visual diff, and structure, then delegates to
    `assemble_golden_report` (which fails closed on any gap)."""
    screenshot_hashes = {
        shot.viewport: shot.content_hash for shot in browser_report.screenshots
    }
    return assemble_golden_report(
        frozen_commit=frozen_commit,
        authority_digest=authority_digest,
        leader_backend=leader_backend,
        worker_backends=list(run_result.worker_backends),
        agent_instance_ids=list(run_result.agent_instance_ids),
        acp_session_ids=list(run_result.acp_session_ids),
        build_evidence=build_evidence,
        test_evidence=test_evidence,
        screenshot_hashes=screenshot_hashes,
        visual_diff=dict(browser_report.visual_diff),
        module_checks=dict(browser_report.structure),
        interaction_checks=dict(browser_report.interactions),
        findings_resolution=findings_resolution,
        sqlite_integrity=run_result.sqlite_integrity,
        permission_lineage=permission_lineage,
        tmux_fidelity=tmux_fidelity,
        diagnostics=diagnostics,
        exit_reentry=exit_reentry,
        final_result=final_result,
        human_acceptance=human_acceptance,
    )


__all__ = [
    "ACPAdapterComposition", "build_acp_adapter_composition",
    "build_product_shell", "run_product_dev",
    "RealPreflightProbe", "run_product_preflight",
    "GoldenRunner", "GoldenRunResult", "build_golden_report",
]
