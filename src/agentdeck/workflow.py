from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pathlib import Path

from .models import AgentSpec, EventRecord, ProjectConfig, new_id, utc_now
from .mission_authority import (
    canonical_workflow_authorized_steps,
    canonical_workflow_plan_hash,
)
from .dispatch_receptive import classify_pane_receptive, receptive_blocker_message
from .runtime.base import RuntimeBackend
from .state import StateStore


REPLY_FIELDS = (
    "handoff_token",
    "status",
    "summary",
    "verification",
    "risks",
    "next_steps",
)
REPLY_STATUSES = {"completed", "blocked", "failed"}
CANONICAL_HANDOFF_FIELDS = {
    "handoff_token",
    "status",
    "summary",
    "verification",
    "risks",
    "next_steps",
    "artifacts",
    "trace_ids",
}
MAX_CANONICAL_HANDOFF_BYTES = 32 * 1024
MAX_CANONICAL_HANDOFF_ITEMS = 64


@dataclass(frozen=True)
class CanonicalArtifact:
    path: str
    content_hash: str | None

    def __post_init__(self) -> None:
        if (
            type(self.path) is not str
            or not self.path
            or (
                self.content_hash is not None
                and (
                    type(self.content_hash) is not str
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", self.content_hash) is None
                )
            )
        ):
            raise ValueError("invalid Worker artifact evidence")


@dataclass(frozen=True)
class CanonicalHandoff:
    handoff_token: str
    status: str
    summary: str
    verification: str
    risks: str
    next_steps: str
    artifacts: tuple[CanonicalArtifact, ...]
    trace_ids: tuple[str, ...]

    def compact(self) -> dict[str, Any]:
        return {
            "handoff_token": self.handoff_token,
            "status": self.status,
            "summary": self.summary,
            "verification": self.verification,
            "risks": self.risks,
            "next_steps": self.next_steps,
            "artifacts": [
                {"path": item.path, "content_hash": item.content_hash}
                for item in self.artifacts
            ],
            "trace_ids": list(self.trace_ids),
        }


def _validated_compact_reply(reply: dict[str, Any]) -> dict[str, str]:
    if type(reply) is not dict:
        raise ValueError("worker reply must be an object")
    status = reply.get("status")
    if status not in REPLY_STATUSES:
        raise ValueError(f"invalid workflow reply status: {status}")
    compact = {"status": status}
    for field in ("summary", "verification", "risks", "next_steps"):
        value = reply.get(field)
        if type(value) is not str or not value.strip():
            raise ValueError(f"worker reply {field} must be a non-empty string")
        compact[field] = value
    return compact


def validate_correlated_workflow_reply(
    reply: dict[str, Any], expected_handoff_token: str
) -> dict[str, str]:
    """Validate the canonical workflow fields and exact dispatch lineage token."""
    if type(expected_handoff_token) is not str or not expected_handoff_token:
        raise ValueError("workflow handoff token is invalid")
    if type(reply) is not dict or reply.get("handoff_token") != expected_handoff_token:
        raise ValueError("workflow handoff token does not match")
    return {
        "handoff_token": expected_handoff_token,
        **_validated_compact_reply(reply),
    }


def build_canonical_handoff(
    *,
    reply: dict[str, Any],
    artifacts: list[dict[str, Any]],
    trace_ids: list[str],
    expected_handoff_token: str,
    require_artifact_hashes: bool = True,
) -> CanonicalHandoff:
    """Build the sole validated compact handoff record used by every transport."""
    correlated = validate_correlated_workflow_reply(reply, expected_handoff_token)
    if type(require_artifact_hashes) is not bool:
        raise ValueError("artifact hash policy is invalid")
    if type(artifacts) is not list:
        raise ValueError("worker artifacts must be a list")
    compact_artifacts: list[CanonicalArtifact] = []
    for artifact in artifacts:
        if type(artifact) is not dict or set(artifact) != {"path", "content_hash"}:
            raise ValueError("worker artifact evidence is invalid")
        path = artifact.get("path")
        content_hash = artifact.get("content_hash")
        if (
            type(path) is not str
            or not path
            or (
                content_hash is None
                and require_artifact_hashes
            )
            or (
                content_hash is not None
                and (
                    type(content_hash) is not str
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash) is None
                )
            )
        ):
            raise ValueError("worker artifact evidence is invalid")
        compact_artifacts.append(CanonicalArtifact(path, content_hash))
    if (
        type(trace_ids) is not list
        or any(type(item) is not str or not item for item in trace_ids)
        or len(trace_ids) != len(set(trace_ids))
    ):
        raise ValueError("worker trace ids are invalid")
    return CanonicalHandoff(
        handoff_token=expected_handoff_token,
        status=correlated["status"],
        summary=correlated["summary"],
        verification=correlated["verification"],
        risks=correlated["risks"],
        next_steps=correlated["next_steps"],
        artifacts=tuple(compact_artifacts),
        trace_ids=tuple(trace_ids),
    )


def validate_canonical_handoff(
    value: object, *, expected_handoff_token: str | None = None
) -> CanonicalHandoff:
    """Rebuild and bound one persisted compact handoff through the sole schema."""
    if type(value) is not dict or set(value) != CANONICAL_HANDOFF_FIELDS:
        raise ValueError("canonical Worker handoff is invalid")
    token = value.get("handoff_token")
    if type(token) is not str or not token:
        raise ValueError("canonical Worker handoff is invalid")
    if expected_handoff_token is not None and token != expected_handoff_token:
        raise ValueError("canonical Worker handoff lineage is invalid")
    artifacts = value.get("artifacts")
    trace_ids = value.get("trace_ids")
    if (
        type(artifacts) is not list
        or type(trace_ids) is not list
        or len(artifacts) > MAX_CANONICAL_HANDOFF_ITEMS
        or len(trace_ids) > MAX_CANONICAL_HANDOFF_ITEMS
    ):
        raise ValueError("canonical Worker handoff is invalid")
    rebuilt = build_canonical_handoff(
        reply={key: value[key] for key in REPLY_FIELDS},
        artifacts=artifacts,
        trace_ids=trace_ids,
        expected_handoff_token=token,
        require_artifact_hashes=True,
    )
    compact = rebuilt.compact()
    if compact != value:
        raise ValueError("canonical Worker handoff is not canonical")
    encoded = json.dumps(compact, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_CANONICAL_HANDOFF_BYTES:
        raise ValueError("canonical Worker handoff exceeds size limit")
    return rebuilt


def validate_compact_worker_outcome(
    *,
    reply: dict[str, Any],
    artifacts: list[dict[str, Any]],
    trace_ids: list[str],
    expected_handoff_token: str,
) -> dict[str, Any]:
    """Compatibility projection of the canonical compact handoff record."""
    return build_canonical_handoff(
        reply=reply,
        artifacts=artifacts,
        trace_ids=trace_ids,
        expected_handoff_token=expected_handoff_token,
        require_artifact_hashes=True,
    ).compact()


def authorized_steps(plan_record: dict[str, Any]) -> list[dict[str, Any]]:
    return canonical_workflow_authorized_steps(plan_record)


def workflow_plan_hash(plan_record: dict[str, Any]) -> str:
    return canonical_workflow_plan_hash(plan_record)


def _reply_blocks(output: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    pending_key: str | None = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        for tui_prefix in ("• ", "› ", "⏺ "):
            if line.startswith(tui_prefix):
                line = line.removeprefix(tui_prefix).lstrip()
                break
        if line.startswith("handoff_token:"):
            if current is not None:
                blocks.append(current)
            current = {"handoff_token": line.split(":", 1)[1].strip()}
            pending_key = None
            continue
        if current is None:
            continue
        key, separator, value = line.partition(":")
        if separator and key.strip() in {*REPLY_FIELDS, "full_output_path"}:
            key = key.strip()
            current[key] = value.strip()
            # 值留空说明它续在下面几行——聊天式 agent 对长句的常态写法。
            pending_key = key if not current[key] else None
            continue
        if pending_key is None:
            continue
        if not line:
            # 续行到此为止:空行之后的内容与该字段无关,绝不吞进来。
            pending_key = None
            continue
        current[pending_key] = (
            f"{current[pending_key]} {line}".strip() if current[pending_key] else line
        )
    if current is not None:
        blocks.append(current)
    return blocks


def parse_correlated_reply(output: str, token: str) -> dict[str, str] | None:
    matching = [
        item for item in _reply_blocks(output) if item.get("handoff_token") == token
    ]
    if not matching:
        return None
    reply = matching[-1]
    for field in REPLY_FIELDS:
        if not reply.get(field):
            return None
    validate_correlated_workflow_reply(reply, token)
    return reply


def build_compact_handoff(
    *,
    step: int,
    agent_id: str,
    reply: dict[str, str],
    reply_id: str,
    artifact_paths: list[str],
) -> dict[str, Any]:
    token = reply.get("handoff_token")
    canonical = build_canonical_handoff(
        reply=reply,
        expected_handoff_token=token if type(token) is str else "",
        artifacts=[
            {"path": path, "content_hash": None}
            for path in artifact_paths
        ],
        trace_ids=[reply_id],
        require_artifact_hashes=False,
    )
    return render_legacy_handoff(
        canonical,
        step=step,
        agent_id=agent_id,
    )


def render_legacy_handoff(
    canonical: CanonicalHandoff,
    *,
    step: int,
    agent_id: str,
) -> dict[str, Any]:
    """Render the historic workflow shape from one validated canonical record."""
    if not isinstance(canonical, CanonicalHandoff):
        raise TypeError("canonical handoff is required")
    if type(step) is not int or step < 1 or type(agent_id) is not str or not agent_id:
        raise ValueError("legacy workflow handoff identity is invalid")
    compact = canonical.compact()
    return {
        "step": step,
        "agent_id": agent_id,
        **{
            field: compact[field]
            for field in ("status", "summary", "verification", "risks", "next_steps")
        },
        "artifact_paths": [item.path for item in canonical.artifacts],
        "trace_command": f"agentdeck trace --id {canonical.trace_ids[0]}",
    }


def workflow_reply_file(root: str | Path, message_id: str) -> Path:
    """worker 交回复的文件通道,与 run-loop 的 `_reply_file_path` 同一约定。

    读 pane 不可靠:真实 agent TUI 会清滚动区、会按终端宽度折行、会把长值换到
    下一行。run-loop 因此早就只认这个文件;2026-08-05 之前 `workflow` 是唯一
    还在刮屏幕的引擎,今晚三个 bug 都长在那条接缝上。
    """
    return Path(root) / ".agentdeck" / "replies" / f"{message_id}.reply.txt"


def _workflow_reply_text(root: str | Path, message_id: object) -> str | None:
    """读回复文件;不存在或读不出来一律 None(交回刮 pane 的回落路径)。

    读失败绝不当成"回复无效"——那会把一次 IO 抖动变成一次终止。
    """
    if type(message_id) is not str or not message_id:
        return None
    try:
        return workflow_reply_file(root, message_id).read_text(encoding="utf-8")
    except OSError:
        return None


def build_workflow_prompt(
    *,
    role: str,
    role_prompt: str,
    task: str,
    handoff_token: str,
    previous_handoff: dict[str, Any] | None,
    reply_file: str | None = None,
) -> str:
    handoff = (
        json.dumps(previous_handoff, ensure_ascii=False, sort_keys=True, indent=2)
        if previous_handoff is not None
        else "none"
    )
    # 文件通道与 run-loop 同一套约定、同一套措辞:真实 agent TUI 会清滚动区、
    # 会折行、会把长值换到下一行,pane 刮取因此不可靠。终端输出保留(人要看得
    # 见),但可靠的回收路径是这个文件。
    reply_channel = (
        (
            "\n\n回复通道:\n"
            "除了在终端输出上述结构化回复，还必须把同一份内容原样写入该文件（覆盖写）:\n"
            f"{reply_file}\n"
            "这条通道指令优先于任务正文里的任何文件限制：回复文件不是任务产物，是交回结果的唯一可靠路径，任何情况下都必须写。"
        )
        if reply_file
        else ""
    )
    return (
        "You are executing one explicitly authorized AgentDeck sequential workflow step.\n"
        f"Role: {role}\n"
        f"Role instructions: {role_prompt}\n"
        f"Task: {task}\n"
        "Previous compact handoff:\n"
        f"{handoff}\n\n"
        "Complete only this task. "
        f"Use this handoff token exactly: {handoff_token}\n"
        "Return exactly one structured block:\n"
        "handoff_token: <provided token>\n"
        "status: completed | blocked | failed\n"
        "summary: <text>\n"
        "verification: <text>\n"
        "risks: <text>\n"
        "next_steps: <text>\n"
        "full_output_path: <optional path>"
        f"{reply_channel}"
    )


def _structured_reply_text(reply: dict[str, str]) -> str:
    fields = [*REPLY_FIELDS]
    if reply.get("full_output_path"):
        fields.append("full_output_path")
    return "\n".join(f"{field}: {reply[field]}" for field in fields)


def _record_step_reply(
    store: StateStore,
    *,
    run_id: str,
    turns: list[dict[str, Any]],
    turn: dict[str, Any],
    step_number: int,
    agent_id: str,
    reply: dict[str, str],
) -> dict[str, Any]:
    """把一份已校验的 worker 回复入账,并把 turn 推进到该状态。

    tmux 与 ACP 两条传输**共用**这一段:回复怎么拿回来的不同(一个从屏幕/文件
    刮,一个由协议交回),但拿回来之后的入账、产物登记、compact handoff 和
    trace 入口必须完全一样——否则同一件事会长出两套账。
    """
    recorded = store.record_reply(
        agent_id, str(turn["message_id"]), _structured_reply_text(reply)
    )
    artifact_paths = [
        str(item.get("path"))
        for item in recorded.get("artifacts", [])
        if item.get("path")
    ]
    handoff = build_compact_handoff(
        step=step_number,
        agent_id=agent_id,
        reply=reply,
        reply_id=str(recorded["reply_id"]),
        artifact_paths=artifact_paths,
    )
    # 成功之后清掉上一次的失败说明:一条 `completed` 的记录同时展示着一句失败,
    # 就是账本在说一件不成立的事(2026-08-06 live 的 codex4 正是如此)。
    turn.pop("blocker", None)
    turn.update(
        {
            "status": reply["status"],
            "reply_id": recorded["reply_id"],
            "handoff": handoff,
            "artifact_paths": artifact_paths,
            "trace_command": f"agentdeck trace --id {recorded['reply_id']}",
            "completed_at": utc_now(),
        }
    )
    store.update_workflow_run(run_id, turns=turns, current_step=step_number + 1)
    if reply["status"] == "completed":
        store.append_event(
            EventRecord.create(
                "workflow_step_completed",
                {
                    "run_id": run_id,
                    "step": step_number,
                    "agent_id": agent_id,
                    "message_id": turn["message_id"],
                    "reply_id": recorded["reply_id"],
                },
            )
        )
    return handoff


def _pending_turn(run_id: str, step_number: int, agent_id: str) -> dict[str, Any]:
    """一条**尚未派发**的 turn。`message_id` 为 None 正是它的凭据。"""
    return {
        "step": step_number,
        "agent_id": agent_id,
        "handoff_token": f"{run_id}_step_{step_number}",
        "status": "pending",
        "message_id": None,
        "job_id": None,
        "reply_id": None,
        "handoff": None,
        "artifact_paths": [],
        "trace_command": None,
        "started_at": utc_now(),
        "completed_at": None,
    }


def _pane_receptive_blocker(
    backend: RuntimeBackend, config: ProjectConfig, agent_id: str, pane_id: str
) -> str | None:
    """读取失败(`unverifiable`)一律放行——那是 runtime 抖动,不是拦阻理由;
    把一次读不出来变成拒绝派发会停掉正常工作。"""
    try:
        pane_text = backend.capture_output(config.runtime, pane_id, 200)
    except Exception:
        pane_text = None
    return receptive_blocker_message(
        classify_pane_receptive(pane_text=pane_text), agent_id, pane_id
    )


def _stop_workflow(
    store: StateStore,
    *,
    run_id: str,
    turns: list[dict[str, Any]],
    turn: dict[str, Any],
    turn_status: str,
    reason: str,
    blocker: str | None = None,
) -> dict[str, Any]:
    turn["status"] = turn_status
    turn["completed_at"] = utc_now()
    if blocker is not None:
        # 停下时说得出**下一步**,而不只是一个枚举值。
        turn["blocker"] = blocker
    record = store.update_workflow_run(
        run_id,
        status="stopped",
        turns=turns,
        stop_reason=reason,
    )
    store.append_event(
        EventRecord.create(
            "workflow_stopped",
            {
                "run_id": run_id,
                "plan_id": record.get("plan_id"),
                "step": turn.get("step"),
                "agent_id": turn.get("agent_id"),
                "reason": reason,
            },
        )
    )
    return record


def _drive_acp_step(
    store: StateStore,
    *,
    run_id: str,
    turns: list[dict[str, Any]],
    existing: dict[str, Any] | None,
    step: Mapping[str, Any],
    step_number: int,
    agent: AgentSpec,
    previous_handoff: dict[str, Any] | None,
    transport_factory: Callable[..., Any],
    sink_factory: Callable[..., Any] | None,
    workspace: str | Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    """把一步交给 ACP worker:请求进去,响应回来,中间没有键盘也没有屏幕。

    与 tmux 那条路的三处**实质**差别:

    1. handoff token 就是 `dispatch_key`——真实 `AcpWorkerTransport` 正是拿它
       去 `parse_correlated_reply` 做关联(而那个解析器就是本模块的,两边本来
       就说同一种回复格式)。
    2. `admit()` 给出**送达回执**。tmux 那条路上没有这个东西:那边的
       "dispatched" 只表示"我们朝那个 pane 打了字",不表示对方收到了。
    3. 完成由协议宣布(`stop_reason == "end_turn"`),不是从屏幕上猜的。判据与
       daemon 侧 `_canonical_transport_result` 一致;协议没这么说就不算完成,
       绝不推断。

    入账之后完全复用 `_record_step_reply`——回复怎么拿回来的不同,拿回来之后
    的账必须一样。
    """
    token = f"dsp_{uuid.uuid4().hex}"
    prompt = build_workflow_prompt(
        role=str(step.get("role") or agent.role),
        role_prompt=agent.role_prompt,
        task=str(step.get("task") or ""),
        handoff_token=token,
        previous_handoff=previous_handoff,
    )
    message_id = new_id("msg")
    dispatch = store.create_dispatch_records(
        "leader",
        agent.agent_id,
        str(step.get("task") or ""),
        prompt,
        # ACP worker 没有 pane。这里记的是协议通道,不是终端。
        f"acp:{agent.agent_id}",
        message_id=message_id,
    )
    turn = existing or _pending_turn(run_id, step_number, agent.agent_id)
    turn.update(
        {
            "handoff_token": token,
            "status": "dispatched",
            "message_id": dispatch["message"]["message_id"],
            "job_id": dispatch["job"]["job_id"],
            "trace_command": f"agentdeck trace --id {dispatch['message']['message_id']}",
        }
    )
    if existing is None:
        turns.append(turn)
    store.update_workflow_run(
        run_id, status="running", current_step=step_number, turns=turns, stop_reason=None
    )
    attempt = {
        "attempt_id": f"{run_id}_step_{step_number}",
        "agent_id": agent.agent_id,
        "dispatch_key": token,
        "configured_transport": "acp",
    }
    # 流式分片的去处。不给就走传输层自己的内存版(用完即丢),给了就落库——
    # 「这个 agent 此刻在做什么」只能从这条流上回答,最终那句 summary 回答不了。
    # `workflow` 不能 import `cli`,所以 sink 由调用方注入,与 transport 同模式。
    sink = (
        sink_factory(agent=agent, attempt=attempt, workspace=workspace)
        if sink_factory is not None
        else None
    )
    transport = transport_factory(
        argv=tuple(agent.transport_command),
        workspace=workspace,
        prompt=prompt,
        **({"sink": sink} if sink is not None else {}),
        # 这一步的预算由引擎说了算。不传的话传输层会用它自己 30 秒的默认值,
        # 于是两个超时各说各话:协议请求早就放弃了,引擎还以为自己在等
        # (2026-08-06 首次真实 ACP 运行里 codex worker 就反复挂在 prompt 阶段)。
        request_timeout=timeout_seconds,
    )

    async def drive() -> Any:
        receipt = await transport.admit(attempt)
        store.append_event(
            EventRecord.create(
                "workflow_step_dispatched",
                {
                    "run_id": run_id,
                    "step": step_number,
                    "agent_id": agent.agent_id,
                    "message_id": turn["message_id"],
                    # 送达回执:tmux 那条路上没有的证据。
                    "receipt_id": receipt.receipt_id,
                    "transport": "acp",
                },
            )
        )
        return await transport.complete(attempt, receipt)

    try:
        result = asyncio.run(drive())
    except Exception as error:  # transport 自己的异常层级由 daemon 定义
        # `completion_stage` 是闭合枚举 {prompt, update, parse, finish, cleanup},
        # 而且刻意不含 provider 输出——它本来就是为了能安全展示给人看而设计的。
        # "失败了"和"在解析阶段失败了"对下一步的指导完全不同,别把它扔掉。
        stage = getattr(error, "completion_stage", None)
        detail = f"{type(error).__name__} at {stage}" if stage else type(error).__name__
        return {
            "stopped": True,
            "record": _stop_workflow(
                store,
                run_id=run_id,
                turns=turns,
                turn=turn,
                turn_status="failed",
                reason="transport_failed",
                blocker=f"ACP worker transport failed: {agent.agent_id} ({detail})",
            ),
        }
    if (
        not getattr(result, "validated", False)
        or getattr(result, "stop_reason", None) != "end_turn"
        or not isinstance(getattr(result, "reply", None), dict)
    ):
        return {
            "stopped": True,
            "record": _stop_workflow(
                store,
                run_id=run_id,
                turns=turns,
                turn=turn,
                turn_status="failed",
                reason="invalid_reply",
                blocker=(
                    "ACP turn did not end with a validated reply: "
                    f"{agent.agent_id} (stop_reason={getattr(result, 'stop_reason', None)!r})"
                ),
            ),
        }
    reply = dict(result.reply)
    try:
        validate_correlated_workflow_reply(reply, token)
    except ValueError as error:
        return {
            "stopped": True,
            "record": _stop_workflow(
                store,
                run_id=run_id,
                turns=turns,
                turn=turn,
                turn_status="failed",
                reason="invalid_reply",
                blocker=f"ACP reply is not correlated: {agent.agent_id} ({error})",
            ),
        }
    handoff = _record_step_reply(
        store,
        run_id=run_id,
        turns=turns,
        turn=turn,
        step_number=step_number,
        agent_id=agent.agent_id,
        reply=reply,
    )
    if reply["status"] != "completed":
        return {
            "stopped": True,
            "record": _stop_workflow(
                store,
                run_id=run_id,
                turns=turns,
                turn=turn,
                turn_status=reply["status"],
                reason=(
                    "worker_blocked" if reply["status"] == "blocked" else "worker_failed"
                ),
            ),
        }
    return {"stopped": False, "handoff": handoff}


def run_sequential_workflow(
    *,
    config: ProjectConfig,
    store: StateStore,
    backend: RuntimeBackend,
    run_id: str,
    poll_interval: float = 0.25,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    acp_worker_transport: Callable[..., Any] | None = None,
    acp_sink_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    record = store.workflow_run_by_id(run_id)
    steps = list(record.get("authorized_steps") or [])
    turns = list(record.get("turns") or [])
    agents = {agent.agent_id: agent for agent in config.agents}
    timeout_seconds = int(record.get("timeout_seconds") or 0)
    previous_handoff = next(
        (
            turn.get("handoff")
            for turn in reversed(turns)
            if turn.get("status") == "completed" and isinstance(turn.get("handoff"), dict)
        ),
        None,
    )

    for step in steps:
        step_number = int(step.get("step") or 0)
        existing = next((turn for turn in turns if turn.get("step") == step_number), None)
        if existing is not None and existing.get("status") == "completed":
            if isinstance(existing.get("handoff"), dict):
                previous_handoff = existing["handoff"]
            continue

        agent_id = str(step.get("agent_id") or "")
        agent = agents.get(agent_id)
        # transport 决定"怎么把任务交给它",必须在任何 pane 操作之前判定:
        # 对一个走协议的 worker,连"pane 存不存在"都不是有意义的问题。
        #
        # 2026-08-05:此前 workflow 完全没有 transport 感知——配置写着
        # `transport = "acp"` 的 worker 照样被往 pane 里打字,而且报告
        # completed。CLAUDE.md 明文禁止 ACP 与 tmux 互相静默 fallback,这正是
        # 那种情形:把一个本可以走协议的 worker,推到当天已经暴露出六个 bug 的
        # "打字进去、读像素出来"那条缝上,还不说一声。
        #
        # 本切片先去掉**静默**:非 tmux transport 一律停下并说清楚,一个字都不
        # 往那个 pane 里打。真正驱动 ACP 派发是下一刀(daemon/transports.py 的
        # `AcpWorkerTransport` 已经实现并通过端到端验收,workflow 还没接上)。
        if agent is not None and agent.transport == "acp" and acp_worker_transport is not None:
            outcome = _drive_acp_step(
                store,
                run_id=run_id,
                turns=turns,
                existing=existing,
                step=step,
                step_number=step_number,
                agent=agent,
                previous_handoff=previous_handoff,
                transport_factory=acp_worker_transport,
                sink_factory=acp_sink_factory,
                workspace=config.root,
                timeout_seconds=timeout_seconds,
            )
            if outcome.get("stopped"):
                return outcome["record"]
            previous_handoff = outcome["handoff"]
            continue
        if agent is not None and agent.transport != "tmux":
            turn = existing or _pending_turn(run_id, step_number, agent_id)
            if existing is None:
                turns.append(turn)
            return _stop_workflow(
                store,
                run_id=run_id,
                turns=turns,
                turn=turn,
                turn_status="pending",
                reason="transport_unsupported",
                blocker=(
                    f"agent is configured for the {agent.transport} transport, which "
                    f"the sequential workflow runner cannot drive yet: {agent_id}; "
                    "AgentDeck will not silently fall back to typing into its pane"
                ),
            )
        binding = store.agent_binding(agent_id)
        if agent is None or not binding or binding.get("status") != "running" or not binding.get("pane_id"):
            turn = existing or {
                "step": step_number,
                "agent_id": agent_id,
                "handoff_token": f"{run_id}_step_{step_number}",
                "status": "pending",
                "message_id": None,
                "job_id": None,
                "reply_id": None,
                "handoff": None,
                "artifact_paths": [],
                "trace_command": None,
                "started_at": utc_now(),
                "completed_at": None,
            }
            if existing is None:
                turns.append(turn)
            return _stop_workflow(
                store,
                run_id=run_id,
                turns=turns,
                turn=turn,
                turn_status="failed",
                reason="agent_unavailable",
            )
        pane_id = str(binding["pane_id"])
        if not backend.pane_exists(config.runtime, pane_id):
            turn = existing or {
                "step": step_number,
                "agent_id": agent_id,
                "handoff_token": f"{run_id}_step_{step_number}",
                "status": "pending",
                "message_id": None,
                "job_id": None,
                "reply_id": None,
                "handoff": None,
                "artifact_paths": [],
                "trace_command": None,
                "started_at": utc_now(),
                "completed_at": None,
            }
            if existing is None:
                turns.append(turn)
            return _stop_workflow(
                store,
                run_id=run_id,
                turns=turns,
                turn=turn,
                turn_status="failed",
                reason="pane_lost",
            )

        # "不重复派发"这条守卫必须认**任务有没有送出去过**,而不是"有没有这条
        # turn 记录"。`pane_lost` / `agent_unavailable` 建的 turn 从未走到
        # send_input,`message_id` 因此是 None——把它当成"已派发"会让每次
        # resume 都去等一个从没问出口的问题,烧满超时后原地不动,而
        # `can_resume: true` 仍宣称这个 run 还能救。2026-08-05 live 连撞两次。
        undelivered = existing is not None and existing.get("message_id") is None
        if existing is None or undelivered:
            # 发送之前先问一句:此刻打进去,会落到该落的地方吗。
            # `send_input` 成功返回**不等于**任务到达——模态框占住键盘时
            # tmux 一声不吭地把按键吃掉,于是 turn 带着 message_id 建好了,
            # 而那句话从没到达(2026-08-04 因此丢了五十分钟)。所以
            # "message_id 是不是 None"盖不住这一类,检查只能在效果之前。
            receptive_blocker = _pane_receptive_blocker(
                backend, config, agent_id, pane_id
            )
            if receptive_blocker is not None:
                turn = existing or _pending_turn(run_id, step_number, agent_id)
                if existing is None:
                    turns.append(turn)
                return _stop_workflow(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    turn_status="pending",
                    reason="pane_not_receptive",
                    blocker=receptive_blocker,
                )
            token = f"{run_id}_step_{step_number}"
            # message_id 必须先铸出来:回复文件以它命名,而 worker 要在提示词
            # 里读到那个路径。`create_dispatch_records` 本来就接受外部 id,
            # 所以存进 message 记录的 prompt 与真正发出去的逐字节相同。
            message_id = new_id("msg")
            prompt = build_workflow_prompt(
                role=str(step.get("role") or agent.role),
                role_prompt=agent.role_prompt,
                task=str(step.get("task") or ""),
                handoff_token=token,
                previous_handoff=previous_handoff,
                reply_file=str(workflow_reply_file(config.root, message_id)),
            )
            dispatch = store.create_dispatch_records(
                "leader",
                agent_id,
                str(step.get("task") or ""),
                prompt,
                pane_id,
                message_id=message_id,
            )
            turn = {
                "step": step_number,
                "agent_id": agent_id,
                "handoff_token": token,
                "status": "dispatched",
                "message_id": dispatch["message"]["message_id"],
                "job_id": dispatch["job"]["job_id"],
                "reply_id": None,
                "handoff": None,
                "artifact_paths": [],
                "trace_command": f"agentdeck trace --id {dispatch['message']['message_id']}",
                "started_at": utc_now(),
                "completed_at": None,
            }
            if undelivered:
                turns[turns.index(existing)] = turn
            else:
                turns.append(turn)
            store.update_workflow_run(
                run_id,
                status="running",
                current_step=step_number,
                turns=turns,
                stop_reason=None,
            )
            try:
                backend.send_input(config.runtime, pane_id, prompt)
            except Exception:
                return _stop_workflow(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    turn_status="failed",
                    reason="pane_lost",
                )
            store.append_event(
                EventRecord.create(
                    "workflow_step_dispatched",
                    {
                        "run_id": run_id,
                        "step": step_number,
                        "agent_id": agent_id,
                        "message_id": turn["message_id"],
                        "pane_id": pane_id,
                    },
                )
            )
        else:
            turn = existing
            token = str(turn.get("handoff_token") or "")

        started = monotonic()
        while True:
            if not backend.pane_exists(config.runtime, pane_id):
                return _stop_workflow(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    turn_status="failed",
                    reason="pane_lost",
                )
            # 文件通道优先:worker 写出的字节不会被终端宽度折行,也不会被
            # TUI 清掉。读不到文件才回落刮 pane——旧回合和还没学会写文件的
            # worker 都仍然能被收回。
            output = _workflow_reply_text(config.root, turn.get("message_id"))
            if output is None:
                output = backend.capture_output(config.runtime, pane_id, lines=400)
            try:
                reply = parse_correlated_reply(output, token)
            except ValueError:
                return _stop_workflow(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    turn_status="failed",
                    reason="invalid_reply",
                )
            if reply is not None:
                handoff = _record_step_reply(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    step_number=step_number,
                    agent_id=agent_id,
                    reply=reply,
                )
                if reply["status"] == "completed":
                    previous_handoff = handoff
                    break
                reason = (
                    "worker_blocked" if reply["status"] == "blocked" else "worker_failed"
                )
                return _stop_workflow(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    turn_status=reply["status"],
                    reason=reason,
                )
            if monotonic() - started >= timeout_seconds:
                return _stop_workflow(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    turn_status="timed_out",
                    reason="timed_out",
                )
            sleeper(poll_interval)

    result = store.update_workflow_run(
        run_id,
        status="completed",
        current_step=len(steps) + 1,
        turns=turns,
        stop_reason=None,
    )
    store.append_event(
        EventRecord.create(
            "workflow_completed",
            {
                "run_id": run_id,
                "plan_id": result.get("plan_id"),
                "step_count": len(steps),
            },
        )
    )
    return result
