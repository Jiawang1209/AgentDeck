from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from collections.abc import Callable
from typing import Any

from ..config import load_config
from ..mission_orchestration import (
    create_mission_preview_from_candidate,
    mission_planning_task,
)
from ..models import EventRecord, ProjectConfig, new_id, utc_now
from ..state import StateStore
from .bindings import execution_digest
from .leader_gateway import (
    CancellationToken,
    LeaderGateway,
    LeaderGatewayError,
    LeaderRequest,
)
from .models import (
    MAX_CONTEXT_BYTES,
    MAX_CONTEXT_TURNS,
    ConversationMutation,
    build_conversation_record,
    build_conversation_transition,
    build_preview_binding_record,
    build_turn_record,
)
from .router import (
    ConversationRouter,
    RoutingContext,
    build_project_setup_preview,
    execute_bound_preview,
)


@dataclass(frozen=True)
class ConversationResponse:
    kind: str
    payload: dict[str, Any]


class ConversationSession:
    def __init__(
        self,
        *,
        root: Path,
        config: ProjectConfig | None = None,
        store: StateStore | None = None,
        leader_gateway: object | None = None,
        router: ConversationRouter | None = None,
        preview_executor: Callable[[str], dict[str, object]] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.config = config
        self.store = store
        self.router = router or ConversationRouter()
        self.leader_gateway = leader_gateway or LeaderGateway()
        self.preview_executor = preview_executor
        self.conversation_id = new_id("cvs")
        self.context_items: list[dict[str, str]] = []
        self.context_bytes = 0
        self.cancel_token = CancellationToken()
        self._preinit_preview: dict[str, object] | None = None
        if self.config is None and (self.root / ".agentdeck" / "config.toml").exists():
            self.config = load_config(self.root)
        if self.config is not None and self.store is None:
            self.store = StateStore.open_existing(self.root)
        if self.config is not None and self.store is not None:
            self._record_session_start()

    def _record_session_start(self) -> None:
        assert self.store is not None
        created = utc_now()
        session = build_conversation_record(self.conversation_id, created_at=created)
        transitions = (
            self._transition("conversation", self.conversation_id, None, "created", "session_started", created),
            self._transition("conversation", self.conversation_id, "created", "ready", "session_ready", created),
        )
        self.store.commit_conversation_mutation(
            ConversationMutation(
                append_records={
                    "conversation_sessions": (session,),
                    "conversation_state_transitions": transitions,
                },
                events=(
                    EventRecord.create(
                        "conversation_session_started",
                        {"conversation_id": self.conversation_id},
                    ),
                ),
            )
        )

    def _transition(
        self,
        entity_type: str,
        entity_id: str,
        from_state: str | None,
        to_state: str,
        reason: str,
        created_at: str | None = None,
    ) -> dict[str, object]:
        return build_conversation_transition(
            transition_id=new_id("cst"),
            conversation_id=self.conversation_id,
            entity_type=entity_type,  # type: ignore[arg-type]
            entity_id=entity_id,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            created_at=created_at or utc_now(),
        )

    def _pending_binding(self) -> dict[str, object] | None:
        if self.store is None:
            return self._preinit_preview.get("binding") if self._preinit_preview else None  # type: ignore[return-value]
        state = self.store.load()
        projection = self.store._conversation_summary(state)
        pending = projection.get("pending_preview")
        if not isinstance(pending, dict):
            return None
        preview_id = pending.get("preview_id")
        for item in state.get("conversation_preview_bindings", []):
            if isinstance(item, dict) and item.get("preview_id") == preview_id:
                return {**item, "state": "pending"}
        return None

    def _leader_ready(self) -> bool:
        if self.config is None or self.config.leader.provider == "missing":
            return False
        describe = getattr(self.leader_gateway, "describe", None)
        if callable(describe):
            try:
                return describe(self.config.leader).readiness == "ready"
            except Exception:
                return False
        return True

    def _remember(self, user_text: str, response: ConversationResponse) -> None:
        item = {"user": user_text, "kind": response.kind}
        size = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
        self.context_items.append(item)
        self.context_bytes += size
        while self.context_items and (
            len(self.context_items) > MAX_CONTEXT_TURNS
            or self.context_bytes > MAX_CONTEXT_BYTES
        ):
            removed = self.context_items.pop(0)
            self.context_bytes -= len(
                json.dumps(removed, ensure_ascii=False).encode("utf-8")
            )

    def _deterministic(self, command: str | None) -> ConversationResponse:
        if command == "status" and self.config is not None and self.store is not None:
            return ConversationResponse("deterministic", asdict(self.store.project_view(self.config)))
        return ConversationResponse(
            "deterministic",
            {"command": command, "safety": "inspect", "executed": False},
        )

    def handle(self, text: str) -> ConversationResponse:
        context = RoutingContext(
            initialized=self.config is not None and self.store is not None,
            leader_ready=self._leader_ready(),
            pending_preview=self._pending_binding(),
        )
        decision = self.router.classify(text, context)
        if decision.kind == "project_setup_preview":
            preview = build_project_setup_preview(
                self.root, now=datetime.now(timezone.utc)
            )
            self._preinit_preview = preview
            response = ConversationResponse(decision.kind, preview)
        elif decision.kind == "deterministic":
            response = self._deterministic(decision.command)
            self._record_compact_terminal_turn("completed", "deterministic_intent")
        elif decision.kind == "leader_setup_preview":
            response = ConversationResponse(
                decision.kind,
                {"blocker": "Leader backend is not ready", "command": "/leader"},
            )
        elif decision.kind == "leader_request":
            response = self._handle_leader(text)
        elif decision.kind == "confirm_preview":
            response = self._confirm_preview(decision.preview_id)
        elif decision.kind == "exit":
            response = ConversationResponse("exit", {"closed": True})
        else:
            response = ConversationResponse(
                decision.kind,
                {"blocker": decision.blocker, "preview_id": decision.preview_id},
            )
        self._remember(text, response)
        return response

    def _record_compact_terminal_turn(self, state: str, reason: str) -> None:
        if self.store is None:
            return
        turn_id = new_id("cvt")
        created = utc_now()
        turn = build_turn_record(
            turn_id, conversation_id=self.conversation_id, created_at=created
        )
        self.store.commit_conversation_mutation(
            ConversationMutation(
                append_records={
                    "conversation_turns": (turn,),
                    "conversation_state_transitions": (
                        self._transition("turn", turn_id, None, "created", "turn_created", created),
                        self._transition("turn", turn_id, "created", "routing", reason, created),
                        self._transition("turn", turn_id, "routing", state, reason, created),
                    ),
                },
                events=(
                    EventRecord.create(
                        "conversation_turn_terminal",
                        {
                            "conversation_id": self.conversation_id,
                            "turn_id": turn_id,
                            "state": state,
                            "reason": reason,
                        },
                    ),
                ),
            )
        )

    def _confirm_preview(self, preview_id: str | None) -> ConversationResponse:
        if self.store is None or self.config is None or self.preview_executor is None:
            return ConversationResponse(
                "blocked", {"blocker": "preview execution adapter is not configured"}
            )
        binding = self._pending_binding()
        if binding is None or binding.get("preview_id") != preview_id:
            return ConversationResponse("blocked", {"blocker": "no pending preview"})
        action_id = binding.get("action_id")
        if not isinstance(action_id, str):
            return ConversationResponse("blocked", {"blocker": "pending preview invalid"})
        try:
            mission = self.store.mission_by_id(action_id)
        except KeyError:
            return ConversationResponse("blocked", {"blocker": "preview target missing"})
        facts = {
            "control_kind": "mission_confirm",
            "project_root": str(self.root),
            "leader_provider": self.config.leader.provider,
            "leader_model": self.config.leader.model,
            "action_id": mission["mission_id"],
            "action_hash": mission["plan_hash"],
        }

        def execute(consumed: dict[str, Any]) -> dict[str, object]:
            result = self.preview_executor(action_id)
            self.store.commit_conversation_mutation(
                ConversationMutation(
                    append_records={
                        "conversation_state_transitions": (
                            self._transition(
                                "preview",
                                str(consumed["preview_id"]),
                                "pending",
                                "consumed",
                                "preview_executed",
                            ),
                            self._transition(
                                "conversation",
                                self.conversation_id,
                                "waiting_confirmation",
                                "busy",
                                "mission_started",
                            ),
                        )
                    },
                    events=(
                        EventRecord.create(
                            "conversation_preview_consumed",
                            {
                                "conversation_id": self.conversation_id,
                                "preview_id": consumed["preview_id"],
                                "mission_id": action_id,
                            },
                        ),
                    ),
                )
            )
            return result

        try:
            result = execute_bound_preview(
                binding,
                facts,
                now=datetime.now(timezone.utc),
                execute=execute,
            )
        except ValueError as error:
            return ConversationResponse("blocked", {"blocker": str(error)})
        return ConversationResponse("preview_executed", dict(result))

    def _handle_leader(self, text: str) -> ConversationResponse:
        assert self.config is not None and self.store is not None
        turn_id = new_id("cvt")
        created = utc_now()
        turn = build_turn_record(
            turn_id, conversation_id=self.conversation_id, created_at=created
        )
        self.store.commit_conversation_mutation(
            ConversationMutation(
                append_records={
                    "conversation_turns": (turn,),
                    "conversation_state_transitions": (
                        self._transition("conversation", self.conversation_id, "ready", "busy", "leader_turn_started", created),
                        self._transition("turn", turn_id, None, "created", "turn_created", created),
                        self._transition("turn", turn_id, "created", "routing", "turn_routed", created),
                        self._transition("turn", turn_id, "routing", "waiting_leader", "leader_requested", created),
                    ),
                },
                events=(EventRecord.create("conversation_turn_started", {"conversation_id": self.conversation_id, "turn_id": turn_id}),),
            )
        )
        selected = tuple(agent.agent_id for agent in self.config.agents)
        planning_task = mission_planning_task(
            text,
            selected_agent_ids=selected,
            step_count=max(2, len(_requested_steps(text))),
        )
        try:
            candidate = self.leader_gateway.generate_mission(
                LeaderRequest(
                    self.config,
                    text,
                    planning_task,
                    180,
                    asdict(self.store.project_view(self.config)).get("skills"),
                ),
                self.cancel_token,
            )
            captured_binding: dict[str, object] = {}

            def mutation_factory(payload: dict[str, object]) -> ConversationMutation:
                facts = {
                    "control_kind": "mission_confirm",
                    "project_root": str(self.root),
                    "leader_provider": self.config.leader.provider,
                    "leader_model": self.config.leader.model,
                    "action_id": payload["mission_id"],
                    "action_hash": payload["plan_hash"],
                }
                now = datetime.now(timezone.utc)
                binding = build_preview_binding_record(
                    new_id("cpv"),
                    conversation_id=self.conversation_id,
                    turn_id=turn_id,
                    preview_kind="mission_confirm",
                    execution_digest=execution_digest(facts),
                    expires_at=(now + timedelta(minutes=10)).isoformat(),
                    created_at=now.isoformat(),
                )
                binding.update(
                    {
                        "action_id": payload["mission_id"],
                        "action_hash": payload["plan_hash"],
                        "leader_provider": self.config.leader.provider,
                        "leader_model": self.config.leader.model,
                        "project_root": str(self.root),
                    }
                )
                captured_binding.update(binding)
                return ConversationMutation(
                    append_records={
                        "conversation_preview_bindings": (binding,),
                        "conversation_state_transitions": (
                            self._transition("turn", turn_id, "waiting_leader", "presenting_preview", "preview_ready"),
                            self._transition("turn", turn_id, "presenting_preview", "completed", "preview_presented"),
                            self._transition("conversation", self.conversation_id, "busy", "waiting_confirmation", "preview_pending"),
                            self._transition("preview", str(binding["preview_id"]), None, "pending", "preview_created"),
                        ),
                    },
                    events=(EventRecord.create("conversation_preview_presented", {"conversation_id": self.conversation_id, "turn_id": turn_id, "preview_id": binding["preview_id"]}),),
                )

            payload = create_mission_preview_from_candidate(
                config=self.config,
                store=self.store,
                candidate=candidate,
                conversation_mutation_factory=mutation_factory,
            )
            payload["preview_binding"] = {
                "preview_id": captured_binding["preview_id"],
                "expires_at": captured_binding["expires_at"],
            }
            return ConversationResponse("mission_preview", payload)
        except LeaderGatewayError as error:
            state = "cancelled" if "cancelled" in str(error).lower() else "failed"
            self.store.commit_conversation_mutation(
                ConversationMutation(
                    append_records={
                        "conversation_state_transitions": (
                            self._transition("turn", turn_id, "waiting_leader", state, state),
                            self._transition("conversation", self.conversation_id, "busy", "ready", "leader_turn_terminal"),
                        )
                    },
                    events=(EventRecord.create("conversation_turn_terminal", {"conversation_id": self.conversation_id, "turn_id": turn_id, "state": state}),),
                )
            )
            return ConversationResponse(state, {"blocker": str(error)})


def _requested_steps(text: str) -> range:
    import re

    match = re.search(r"共\s*(\d+)\s*轮", text)
    count = int(match.group(1)) if match else 2
    return range(count)
