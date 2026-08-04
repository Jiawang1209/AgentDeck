"""Single-instance record, JSONL log and pid probe for the run-loop host.

背景宿主让已验证的单 wave 引擎在脱离客户端的进程里继续跑(round 12
八次手动重启 follow 段的痛点)。本模块只管进程记录/日志/存活探测这一层
(user 拍板:只复用 pidfile+日志+单例互斥,不引入 socket/lease),不含调度
逻辑、不 import cli,也绝不触碰 M2 Mission daemon。
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

HOST_DIR_NAME = "run-loop-host"
HOST_RECORD_NAME = "host.json"
HOST_LOG_NAME = "host.log"

# 闭合枚举:每个值在 status 里都对应一条显式后续命令。
RUN_LOOP_HOST_STOPPED_REASONS = (
    "gate_reached",  # wave gate 不再是 waiting_for_reply
    "budget_exhausted",  # 达 --max-waves 上限而仍在等回复
    "policy_revoked",  # approval_mode 不再是 autonomous(远程刹车)
    "signalled",  # run-loop-host stop 的 SIGTERM 在本 wave 结束后被接受
    "engine_error",  # wave 引擎抛异常(只记异常类型)
    "human_gate",  # 被等待的 worker 停在未委托授权框上(等待永不会自解)
)


def host_dir(root: Path) -> Path:
    return Path(root) / ".agentdeck" / HOST_DIR_NAME


def host_record_path(root: Path) -> Path:
    return host_dir(root) / HOST_RECORD_NAME


def host_log_path(root: Path) -> Path:
    return host_dir(root) / HOST_LOG_NAME


def read_host_record(root: Path) -> dict[str, Any] | None:
    """读单例记录;缺失、不可读或损坏一律 None(调用方按"无宿主"处理)。"""
    try:
        text = host_record_path(root).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        record = json.loads(text)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def write_host_record(root: Path, record: dict[str, Any]) -> None:
    """原子替换写入(读者永不看到半个 JSON)。"""
    directory = host_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = host_record_path(root)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但不属于本用户
    except OSError:
        return False
    return True


def host_liveness(
    root: Path, probe: Callable[[int], bool] = pid_alive
) -> tuple[dict[str, Any] | None, bool, bool]:
    """返回 (record, running, stale)。

    running=pid 存活;stale=记录声称有 pid 但进程已死(需 stop 清理)。
    pid 已被清空的干净停止记录既不 running 也不 stale。
    `probe` 可注入,使 CLI 层与测试共用同一份判定逻辑(单一来源)。
    """
    record = read_host_record(root)
    if record is None:
        return None, False, False
    pid = record.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return record, False, False
    alive = probe(pid)
    return record, alive, not alive


def append_host_log(root: Path, entry: dict[str, Any]) -> None:
    """追加一行 JSONL;跨宿主共享同一文件,历史永不被截断或重写。"""
    directory = host_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    with host_log_path(root).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


# 人类门证据的身份键:同一道框判定用,waiting_hint 是展示文本不参与身份。
_HUMAN_GATE_IDENTITY = ("agent_id", "box_kind", "command", "mcp_server", "mcp_tool")
# 公开的证据字段清单:host 记录/日志/审计/status 契约共用同一份,
# 契约层直接 import 本元组,绝不另抄一份。
HUMAN_GATE_FIELDS = (*_HUMAN_GATE_IDENTITY, "waiting_hint")


# 构成人类门的闭合理由集。判据 1 消费它。
HUMAN_GATE_REASONS = (
    # 屏上有一道框,但没有任何活跃委托覆盖它——grant 一条就能让它自动放行。
    "no active delegation",
    # 首次目录 trust 框。它**结构上永远不可委托**(CLAUDE.md 明写这是 human
    # setup,不得由 worker 输入或静默 Enter 绕过),所以绝不能复用上面那句
    # ——"no active delegation" 会暗示"grant 一条就好了",而那条 grant 并不
    # 存在也不该存在。假的补救指引比没有指引更糟。
    "directory trust is human setup",
)


def human_gate_candidate(
    skipped: list[dict[str, Any]], awaiting_agents: set[str]
) -> dict[str, Any] | None:
    """从一次框扫描的 skipped 项里挑出人类门候选。

    四条判据全部成立才算候选:

    1. `reason` 落在闭合的 `HUMAN_GATE_REASONS` 内 —— pane capture 失败是
       runtime 抖动而非人类门;
    2. agent 落在本 plan 的 awaiting 集内 —— 别的 plan、闲置 agent 身上的
       框不该停掉这台宿主;
    3. `box_pending` —— 屏上确有一道**待批**框(活动选择器字形)。终审
       2026-08-01 发现:已答复的折叠框(`… ? -> Yes`)同样会命中
       `_detect_waiting_for_input` 的 marker,若不要求这道正证明,一道
       早已答复的框就能停掉一个健康的走开段;
    4. `box_kind` 非空 —— 解析不出框身份时不判定,这是 spec 冻结的
       fail-open 条款(全 None 身份恒等于自身,会让 debounce 必然确认)。

    任何一条不成立就跳过该项;全都不成立返回 None——fail-open 到既有
    轮询行为,宁可多转也绝不误停一个正常的走开段。
    """
    for item in skipped:
        if not isinstance(item, dict):
            continue
        if item.get("reason") not in HUMAN_GATE_REASONS:
            continue
        agent_id = item.get("agent_id")
        if agent_id not in awaiting_agents:
            continue
        if not item.get("box_pending"):
            continue
        if not item.get("box_kind"):
            continue
        return {field: item.get(field) for field in HUMAN_GATE_FIELDS}
    return None


def human_gate_next_command(gate: dict[str, Any] | None) -> str | None:
    """人类门下唯一成立的后续动作:去那道框所在的 pane 看一眼。

    这是 `--follow` 与 `run-loop-host status` 共用的**单一来源**:两面都
    只调用本函数,绝不各自拼一遍字符串。

    指针复用既有的只读卡片 `agentdeck agent terminal --agent <id>`——它只
    渲染 tmux attach / select-pane 命令文本,自己不 attach、不 select、
    不 capture、不 send、不写 state。AgentDeck 依然永不代按那道框:按它
    始终是人类在那个 pane 里的动作。

    无门、身份解析不出 `agent_id` 时返回 None(调用方保持原有 next_command)。
    """
    if not isinstance(gate, dict):
        return None
    agent_id = gate.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id:
        return None
    return f"agentdeck agent terminal --agent {agent_id}"


def same_human_gate(
    left: dict[str, Any] | None, right: dict[str, Any] | None
) -> bool:
    """两次扫描看到的是否是同一道框(debounce 用)。"""
    if not left or not right:
        return False
    return all(left.get(key) == right.get(key) for key in _HUMAN_GATE_IDENTITY)
