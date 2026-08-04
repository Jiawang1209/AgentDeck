"""Conservative shell normalization for command-prefix delegation matching.

round 12 live 发现 #3:env 前缀赋值、for 循环包装、多命令链会逃出
`command_prefix` 委托的 startswith 匹配。本模块把复合命令保守拆段,
要求每一段要么命中该 agent 的某条活跃委托前缀,要么属于内置固定胶水
白名单,且至少一段命中真实委托。任何解析不了的形态一律返回 None
(fail-closed:调用方回落到现行人工路径)。纯函数,不读 state、不碰
runtime。
"""
from __future__ import annotations

import re
from typing import NamedTuple, Sequence


class MatchedSegment(NamedTuple):
    segment: str
    via: str  # 命中的委托前缀原文,或字面量 "glue"


class CompositeMatch(NamedTuple):
    segments: tuple[MatchedSegment, ...]


# 命令替换/进程替换/heredoc:出现即整体拒绝(原文扫描,先于拆段)
_HARD_REJECT_SUBSTRINGS = ("$(", "`", "<(", ">(", "<<")
_CONTROL_PREFIX_WORDS = ("do", "then", "else")
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S+$")
_GLUE_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")
_SIMPLE_WORD = re.compile(r"^(?:[A-Za-z0-9._\-]+|\$\{[A-Za-z_][A-Za-z0-9_]*\})$")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REDIRECT_TOKEN = re.compile(r"^(?:\d*>{1,2})(.*)$")
_TMP_TARGET = re.compile(r"^/tmp/\S+$")


def _split_top_level(command: str) -> list[str] | None:
    """引号感知的顶层拆段:在 ;、&&、||、|、换行处切分。

    不配对引号、单 &(后台执行)、顶层 <(输入重定向)→ None。
    引号内的分隔符不拆;反斜杠转义原样吞并下一字符。
    """
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if ch == "\\":
            buf.append(command[i : i + 2])
            i += 2
            continue
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "\n":
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        if command.startswith("&&", i) or command.startswith("||", i):
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        if ch == "&":
            if i > 0 and command[i - 1] == ">":
                buf.append(ch)  # 2>&1 的 >& 形态
                i += 1
                continue
            return None
        if ch == "<":
            return None
        if ch in (";", "|"):
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if quote is not None:
        return None
    segments.append("".join(buf))
    return [seg.strip() for seg in segments if seg.strip()]


def _strip_control_prefix(segment: str) -> str:
    tokens = segment.split()
    while tokens and tokens[0] in _CONTROL_PREFIX_WORDS:
        tokens = tokens[1:]
    return " ".join(tokens)


def _strip_redirects(tokens: list[str]) -> list[str] | None:
    """剥离并校验重定向:仅允许 2>&1 与目标为 /tmp/ 下(无 ..)的
    `[fd]>`/`[fd]>>`。任何其它带 `>` 的 token 一律硬拒——spec 审查发现
    两条 fail-open:①非 2 号 fd 前缀(`1>>` `3>` `10>`)曾漏过 /tmp 约束;
    ②shell 认 `word>target`(如 `echo foo>/etc/evil`)为重定向,而按空白
    切 token 后它形如普通参数。`>` 只应出现在被本函数识别并校验的重定向
    里,出现在别处就是我们没理解的形态。"""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "2>&1":
            i += 1
            continue
        m = _REDIRECT_TOKEN.match(tok)
        if m is not None:
            target = m.group(1)
            if not target:
                if i + 1 >= len(tokens):
                    return None
                target = tokens[i + 1]
                i += 2
            else:
                i += 1
            if _TMP_TARGET.match(target) is None or ".." in target:
                return None
            continue
        if ">" in tok:
            return None
        out.append(tok)
        i += 1
    return out


def _strip_env_assignments(tokens: list[str]) -> list[str] | None:
    i = 0
    while i < len(tokens) and _ENV_ASSIGNMENT.match(tokens[i]):
        value = tokens[i].split("=", 1)[1]
        if value[0] in "'\"":
            return None  # 带引号 value 无法安全按空白切 token:整体拒
        i += 1
    return tokens[i:]


def _is_glue(tokens: list[str]) -> bool:
    """内置固定胶水白名单 v1(不可配置;扩名单=显式改码+过测试)。"""
    if not tokens:
        return True
    head = tokens[0]
    if len(tokens) == 1 and head in ("done", "fi"):
        return True
    if len(tokens) == 1 and _GLUE_ASSIGNMENT.match(head):
        return True
    if head in ("echo", "true", "test"):
        return True
    if head == "exit":
        return len(tokens) <= 2
    if head == "[":
        return tokens[-1] == "]"
    if head == "if":
        return len(tokens) >= 3 and tokens[1] == "[" and tokens[-1] == "]"
    if head == "for":
        return (
            len(tokens) >= 3
            and _NAME.match(tokens[1]) is not None
            and tokens[2] == "in"
            and all(_SIMPLE_WORD.match(tok) for tok in tokens[3:])
        )
    if head in ("tail", "head"):
        for tok in tokens[1:]:
            if tok.startswith("-"):
                continue
            if not tok.startswith("/tmp/") or ".." in tok:
                return False
        return True
    return False


def is_composite_command(command: str) -> bool:
    """True 当命令不是"单一简单命令":含顶层分隔符,或含本模块无法解析的
    shell 形态(命令替换/heredoc/输入重定向/后台 &/不配对引号)。

    调用方据此判断"平前缀 startswith 的结论是否可信"。对复合命令而言
    首段命中委托并不意味着整条安全——`node tests/x.mjs; rm -rf /` 的
    startswith 会命中 `node tests/` 委托,而尾段是任意命令(spec
    "Danger boundary (hard requirement)")。这类命令只能走逐段覆盖匹配,
    解析不了即整体不匹配(fail-closed,回落人工路径)。

    含重定向的单一命令同样不可信(评审发现,先于本功能存在的 fail-open):
    `node tests/x.mjs > /etc/evil` 的 startswith 命中 `node tests/`,但写
    目标是任意路径。交给归一化后重定向受 `/tmp` 约束——合法的
    `> /tmp/x.log` 仍匹配(以 composite 形态),越界的整体拒。
    """
    for marker in _HARD_REJECT_SUBSTRINGS:
        if marker in command:
            return True
    if ">" in command:
        return True
    segments = _split_top_level(command)
    if segments is None:
        return True
    return len(segments) > 1


def normalize_match(
    command: str,
    prefixes: Sequence[str],
    *,
    exact_commands: Sequence[str] = (),
) -> CompositeMatch | None:
    """拆段+逐段覆盖匹配;任何解析失败返回 None(fail-closed)。

    两种委托形态在这里汇合:

    - `prefixes` 是 `startswith`——前缀钉住开头,**尾巴可以是任何东西**。
    - `exact_commands` 是等值——整条命令都被钉住,没有尾巴。

    后者是 round 1 finding F2 的修复:gate-preview 梯子第 1 级把整条命令当
    前缀,并向人声称"连带授权:(无——仅此一条命令)",而在纯 `startswith` 下
    这句话不成立——`curl <url> -o <路径>` / `-d @<文件>` 都以它开头,都会命中,
    而这两个都不是 shell 重定向,既有硬拒一条也不适用。等值形态让那句话
    变成真的,也让"只授权这一条命令"第一次成为一个**存在的档位**。

    两侧都按单空格归一后比较:框文本折行会折叠空白,这与命令提取处"匹配时
    空白折叠兜住折行歧义"同一取舍。
    """
    if not command or not command.strip():
        return None
    active = [prefix for prefix in prefixes if prefix]
    active_exact = [
        " ".join(item.split()) for item in exact_commands if item and item.strip()
    ]
    if not active and not active_exact:
        return None
    for marker in _HARD_REJECT_SUBSTRINGS:
        if marker in command:
            return None
    raw_segments = _split_top_level(command)
    if not raw_segments:
        return None
    matched: list[MatchedSegment] = []
    covered_any = False
    for raw in raw_segments:
        stripped = _strip_control_prefix(raw)
        if not stripped:
            matched.append(MatchedSegment(raw, "glue"))
            continue
        tokens = stripped.split()
        if tokens[0] in ("eval", "source"):
            return None
        after_redirects = _strip_redirects(tokens)
        if after_redirects is None:
            return None
        after_env = _strip_env_assignments(after_redirects)
        if after_env is None:
            return None
        if _is_glue(after_env):
            matched.append(MatchedSegment(raw, "glue"))
            continue
        rest = " ".join(after_env)
        # 等值先判:它是最窄的一档,命中即定,不必再看有没有更宽的前缀覆盖它。
        via = next((item for item in active_exact if rest == item), None)
        if via is None:
            via = next((prefix for prefix in active if rest.startswith(prefix)), None)
        if via is None:
            return None
        covered_any = True
        matched.append(MatchedSegment(raw, via))
    if not covered_any:
        return None
    return CompositeMatch(tuple(matched))
