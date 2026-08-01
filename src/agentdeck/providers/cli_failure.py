"""Closed-enum classification of CLI-backed Leader failures.

CLI provider 失败此前只报 `nonzero`,真实原因(额度耗尽/未登录/模型不可用)
躺在 provider 输出里,而安全边界禁止留存那些文本——操作者因此拿不到任何
线索(2026-08-01 round 14:`[leader.orchestrator] = claude-cli` 因
"You're out of usage credits" 连续失败,审计只有 `nonzero`)。

本模块沿用本项目已验证的授权框提取器模式:**解析 → 分类 → 丢弃原文**。
它只读输入、只返回闭合枚举码,绝不回传、绝不存储任何 provider 文本片段;
调用方也只把返回的码写进审计。分类是**诊断信息**,不是授权,也不改变任何
gate、重试或回落行为。
"""
from __future__ import annotations

# 闭合枚举:新增值必须同步测试与文档。
CLI_FAILURE_REASONS = (
    "credits_exhausted",
    "auth_required",
    "model_unavailable",
    "rate_limited",
    "unknown",
)

# 只扫有界前缀:CLI 的失败提示总在开头,不为分类去读整篇输出。
_SCAN_LIMIT_CHARS = 4096

# 允许列表:小而具体的公开失败措辞;顺序即优先级。
_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "credits_exhausted",
        ("out of usage credits", "usage limit reached", "insufficient credits"),
    ),
    (
        "auth_required",
        ("not logged in", "/login", "unauthorized", "authentication failed",
         "please authenticate"),
    ),
    (
        "model_unavailable",
        ("unknown model", "model not found", "invalid model", "unsupported model"),
    ),
    ("rate_limited", ("rate limit", "too many requests", "429")),
)


def classify_cli_failure(stdout: object, stderr: object) -> str:
    """把 CLI 失败输出分类成闭合枚举码;认不出即 `unknown`。

    输入只被读取与匹配,函数**永不**回传其中任何片段。
    """
    haystack = ""
    for chunk in (stdout, stderr):
        if isinstance(chunk, str) and chunk:
            haystack += chunk[:_SCAN_LIMIT_CHARS].lower() + "\n"
    if not haystack:
        return "unknown"
    for reason, markers in _MARKERS:
        if any(marker in haystack for marker in markers):
            return reason
    return "unknown"
