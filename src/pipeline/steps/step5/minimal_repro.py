# -*- coding: utf-8 -*-
"""最小复现路径（D3，L0→L2→L3 收敛）—— 从失败用例的执行步骤中提取最小复现路径。

职责：
  - extract_minimal_repro：输入执行步骤列表，定位首个失败步骤，
    保留导航/登录/数据准备等前置必需步骤，移除截图/探索等非必需步骤，
    返回最小复现路径 + 压缩比 + 人类可读摘要。（L2，保持向后兼容）
  - converge_minimal_repro：★ D3 收敛增强 —— 在 L2 结果之上进一步收敛为
    「≤3 步关键路径 + 前置条件清单 + 触发条件 + 断言」的结构化最小复现清单。
    被挤出关键路径的必需步骤不会丢失，而是折叠为前置条件（保证路径仍可复现）。
  - converge_repro_strings：★ 面向 Step5 Bug 报告的字符串步骤（_bug_report._load_case_steps
    回放出的 list[str]）做同样的收敛，供 triage 出的失败用例直接消费。
  - build_repro_section / build_converged_section：渲染为 Markdown 报告区块。

纯读分析：零 DB 写、零 DDL。所有数据来自调用方传入的步骤列表。

L2 算法（extract_minimal_repro）：
  1. 定位首个失败步骤（failure_point）
  2. 从失败步骤向前回溯，保留必需前置步骤（navigate/login/fill/select/click）
  3. 移除非必需步骤（screenshot/verify/wait/explore）
  4. 返回最小路径 + 压缩比 + 摘要

L3 收敛算法（converge_minimal_repro，CAPABILITY_GAP_PRD D3「20 步收敛成 3 步」）：
  1. 复用 L2 去冗余结果（已移除截图/验证/等待/探索）
  2. 连续重复动作去重（同 action_type + 同 target 连续出现 → 保留一条）
  3. 尾部切分：最靠近失败点的 ≤max_key_steps 步为「关键路径」
  4. 被挤出关键路径的必需步骤 → 归一化折叠为「前置条件」（不丢失可复现性）
  5. 失败步骤本身 → 触发条件（trigger）+ 断言（expected/actual）
  6. 输出收敛率 convergence_ratio = dropped / original（PRD 要求 ≥50%）

[未复核-需 GLM/DeepSeek 把关]（converge_* 收敛逻辑，寇豆码 新增）
"""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

# 必需动作类型（导航/认证/数据准备/交互 —— 移除后复现路径断裂）
_ESSENTIAL_ACTIONS: frozenset[str] = frozenset(
    {
        "navigate",
        "goto",
        "login",
        "fill",
        "input",
        "select",
        "click",
        "submit",
        "upload",
        "download",
        "drag",
        "drop",
        "switch_tab",
        "switch_frame",
        "iframe_enter",
        "iframe_exit",
        "press_key",
        "keyboard",
        "scroll_into_view",
    }
)

# 非必需动作类型（截图/验证/等待/探索 —— 移除不影响复现）
_NON_ESSENTIAL_ACTIONS: frozenset[str] = frozenset(
    {
        "screenshot",
        "snapshot",
        "verify",
        "check",
        "assert",
        "wait",
        "sleep",
        "explore",
        "discover",
        "hover_probe",
        "log",
        "record",
        "measure",
    }
)


def _normalize_action_type(action_type: str) -> str:
    """归一化动作类型为小写无下划线。"""
    return (action_type or "").strip().lower().replace("-", "_")


def _is_essential(action_type: str) -> bool:
    """判断动作是否为必需（导航/认证/交互）。"""
    norm = _normalize_action_type(action_type)
    if norm in _ESSENTIAL_ACTIONS:
        return True
    # 模糊匹配：包含 navigate/login/click/fill/select/submit/upload 关键词
    for kw in (
        "navigate",
        "goto",
        "login",
        "click",
        "fill",
        "select",
        "submit",
        "upload",
        "drag",
        "iframe",
        "switch",
        "press",
    ):
        if kw in norm:
            return True
    return False


def _find_failure_point(steps: list[dict[str, Any]]) -> int:
    """定位首个失败步骤的索引。返回 -1 表示无失败。"""
    for i, step in enumerate(steps):
        status = str(step.get("status", "")).strip().lower()
        if status in ("failed", "fail", "error", "blocked"):
            return i
    return -1


def _build_summary(steps: list[dict[str, Any]], failure_step: dict[str, Any]) -> str:
    """构建人类可读的复现摘要。"""
    parts: list[str] = []
    for i, step in enumerate(steps, 1):
        desc = step.get("action_desc") or step.get("action_type", f"step_{i}")
        parts.append(f"{i}. {desc}")

    error = failure_step.get("error") or failure_step.get("error_message", "")
    error_line = f"\n失败: {error}" if error else "\n失败: (无错误信息)"

    return "复现路径:\n" + "\n".join(parts) + error_line


def build_repro_section(repro_result: dict[str, Any]) -> str:
    """把最小复现结果渲染为 Markdown 报告区块。

    Args:
        repro_result: extract_minimal_repro 的返回值

    Returns:
        Markdown 格式的复现路径区块字符串
    """
    if not repro_result.get("has_repro"):
        return "## 最小复现路径\n\n无失败步骤，无需复现。\n"

    lines = [
        "## 最小复现路径",
        "",
        f"> 原始 {repro_result['original_count']} 步 → 最小 {repro_result['minimal_count']} 步"
        f"（压缩比 {repro_result['compression_ratio']:.1%}）",
        "",
        "```",
        repro_result["repro_summary"],
        "```",
        "",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# ★ D3 收敛增强（L3）：≤3 步关键路径 + 前置条件 + 触发条件 + 断言
#   [未复核-需 GLM/DeepSeek 把关]
# ═══════════════════════════════════════════════════════════════════════════

REPRO_SCHEMA_VERSION = "minimal-repro-v2"

# 关键路径默认上限（PRD D3：「20 步收敛成 3 步」）
DEFAULT_MAX_KEY_STEPS = 3

# 前置条件归一化前缀（按动作语义分类）
_PRECOND_NAV_KEYS = ("navigate", "goto", "open_url", "visit")
_PRECOND_AUTH_KEYS = ("login", "auth", "sign_in", "signin")
_PRECOND_DATA_KEYS = ("upload", "seed", "prepare", "fixture", "import")


def _step_target(step: dict[str, Any]) -> str:
    """提取步骤的目标标识（target/selector/element/locator 任一）。"""
    for key in ("target", "selector", "element", "locator", "url"):
        value = step.get(key)
        if value:
            return str(value).strip()
    return ""


def _step_label(step: dict[str, Any], fallback_index: int = 0) -> str:
    """提取步骤的人类可读描述（action_desc 优先，回落 action_type + target）。"""
    desc = str(step.get("action_desc") or step.get("desc") or "").strip()
    if desc:
        return desc
    action_type = str(step.get("action_type") or "").strip()
    target = _step_target(step)
    if action_type and target:
        return f"{action_type} {target}"
    return action_type or target or f"step_{fallback_index}"


def _dedup_consecutive(steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """去除连续重复动作（同 action_type + 同 target 连续出现只保留首条）。

    Args:
        steps: 已去冗余的必需步骤列表。

    Returns:
        (去重后步骤列表, 被去重的步骤数)
    """
    kept: list[dict[str, Any]] = []
    removed = 0
    last_key: tuple[str, str] | None = None
    for step in steps:
        key = (
            _normalize_action_type(str(step.get("action_type") or "")),
            _step_target(step),
        )
        # 仅当 target 非空时才做连续去重（target 为空无法判定是否真重复）
        if last_key is not None and key == last_key and key[1]:
            removed += 1
            continue
        kept.append(step)
        last_key = key
    return kept, removed


def _to_precondition(step: dict[str, Any], index: int) -> str:
    """把一个必需步骤归一化为一条前置条件文案。"""
    action_type = _normalize_action_type(str(step.get("action_type") or ""))
    label = _step_label(step, index)
    if any(k in action_type for k in _PRECOND_AUTH_KEYS):
        return f"已登录：{label}"
    if any(k in action_type for k in _PRECOND_NAV_KEYS):
        return f"已打开页面：{label}"
    if any(k in action_type for k in _PRECOND_DATA_KEYS):
        return f"已准备数据：{label}"
    return f"已完成前置操作：{label}"


def _dedup_preserve_order(items: list[str]) -> list[str]:
    """保序去重字符串列表。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _extract_assertion(
    failure_step: dict[str, Any],
    expected: Any = None,
    actual: Any = None,
) -> dict[str, Any]:
    """从失败步骤（或调用方显式传入）提取断言三元组。"""
    _expected = expected if expected is not None else failure_step.get("expected")
    _actual = actual if actual is not None else failure_step.get("actual")
    return {
        "check_type": str(failure_step.get("check_type") or "") or "-",
        "expected": None if _expected is None else str(_expected),
        "actual": None if _actual is None else str(_actual),
        "error": str(
            failure_step.get("error") or failure_step.get("error_message") or ""
        ),
    }


def converge_minimal_repro(
    steps: list[dict[str, Any]],
    max_key_steps: int = DEFAULT_MAX_KEY_STEPS,
    expected: Any = None,
    actual: Any = None,
    precondition: str = "",
) -> dict[str, Any]:
    """把失败用例的执行步骤收敛为「≤N 步关键路径 + 前置条件」的最小复现清单。

    与 :func:`extract_minimal_repro` 的区别：后者只做「去冗余」（保留全部必需步骤），
    本函数在其之上进一步做「收敛」—— 关键路径压到 ``max_key_steps`` 步以内，
    被挤出的必需步骤折叠为前置条件，因此复现路径信息不丢失但信噪比大幅提升。

    Args:
        steps: 执行步骤列表，每步至少含 action_type / action_desc / status；
            可选 target / selector / expected / actual / check_type / error。
        max_key_steps: 关键路径步数上限（默认 3，PRD D3 口径）。至少为 1。
        expected: 显式期望值（缺省时从失败步骤读取）。
        actual: 显式实际值（缺省时从失败步骤读取）。
        precondition: 用例自带的前置条件文案（如 test_cases.precondition），
            会置于前置条件清单首位。

    Returns:
        结构化最小复现清单，键：
        - schema_version: schema 版本标识
        - has_repro: 是否存在可复现路径
        - preconditions: list[str] 前置条件清单（含折叠进来的必需步骤）
        - key_steps: list[dict] 关键路径（≤max_key_steps 步，含失败步骤）
        - key_step_count: 关键路径步数
        - trigger: dict 直接触发失败的动作
        - assertion: dict 断言（check_type/expected/actual/error）
        - failure_point: 失败步骤在原列表中的索引（-1 = 无失败）
        - original_count / kept_count / dropped_count
        - dropped_breakdown: 各类丢弃原因计数
        - convergence_ratio: 收敛率 = dropped_count / original_count
        - repro_summary: 人类可读摘要
    """
    max_key_steps = max(1, int(max_key_steps or DEFAULT_MAX_KEY_STEPS))
    base_preconditions = [precondition.strip()] if precondition and precondition.strip() else []

    empty_result: dict[str, Any] = {
        "schema_version": REPRO_SCHEMA_VERSION,
        "has_repro": False,
        "preconditions": base_preconditions,
        "key_steps": [],
        "key_step_count": 0,
        "trigger": None,
        "assertion": {"check_type": "-", "expected": None, "actual": None, "error": ""},
        "failure_point": -1,
        "original_count": len(steps or []),
        "kept_count": 0,
        "dropped_count": 0,
        "dropped_breakdown": {
            "non_essential": 0,
            "deduped": 0,
            "folded_into_preconditions": 0,
        },
        "convergence_ratio": 0.0,
        "repro_summary": "无失败步骤，无需复现" if steps else "无执行步骤",
    }
    if not steps:
        return empty_result

    failure_point = _find_failure_point(steps)
    if failure_point < 0:
        return empty_result

    failure_step = steps[failure_point]
    original_count = len(steps)

    # ── 1. 去冗余：失败点之前只保留必需步骤（复用 L2 判定） ──
    essential_prefix: list[dict[str, Any]] = []
    non_essential_dropped = 0
    for i in range(failure_point):
        if _is_essential(str(steps[i].get("action_type", ""))):
            essential_prefix.append(steps[i])
        else:
            non_essential_dropped += 1
    # 失败点之后的步骤对复现无意义，一并计入丢弃
    non_essential_dropped += original_count - failure_point - 1

    # ── 2. 连续重复动作去重 ──
    essential_prefix, deduped = _dedup_consecutive(essential_prefix)

    # ── 3. 尾部切分：最靠近失败点的 (max_key_steps - 1) 步 + 失败步骤 = 关键路径 ──
    tail_budget = max_key_steps - 1
    if tail_budget > 0:
        folded = essential_prefix[:-tail_budget] if tail_budget < len(essential_prefix) else []
        tail = essential_prefix[-tail_budget:] if tail_budget < len(essential_prefix) else list(essential_prefix)
    else:
        folded = list(essential_prefix)
        tail = []

    # ── 4. 被挤出的必需步骤 → 折叠为前置条件（不丢失可复现性） ──
    preconditions = list(base_preconditions)
    for idx, step in enumerate(folded):
        preconditions.append(_to_precondition(step, idx))
    preconditions = _dedup_preserve_order(preconditions)

    # ── 5. 组装关键路径（含失败步骤） ──
    key_steps: list[dict[str, Any]] = []
    for seq, step in enumerate(tail + [failure_step], 1):
        key_steps.append(
            {
                "seq": seq,
                "action_type": str(step.get("action_type") or ""),
                "action_desc": _step_label(step, seq),
                "target": _step_target(step),
                "status": str(step.get("status") or ""),
                "is_failure": step is failure_step,
            }
        )

    # ── 6. 触发条件 + 断言 ──
    trigger = {
        "action_type": str(failure_step.get("action_type") or ""),
        "action_desc": _step_label(failure_step, failure_point),
        "target": _step_target(failure_step),
        "failure_point_index": failure_point,
    }
    assertion = _extract_assertion(failure_step, expected, actual)

    kept_count = len(key_steps)
    dropped_count = original_count - kept_count
    convergence_ratio = dropped_count / original_count if original_count > 0 else 0.0

    result = {
        "schema_version": REPRO_SCHEMA_VERSION,
        "has_repro": True,
        "preconditions": preconditions,
        "key_steps": key_steps,
        "key_step_count": kept_count,
        "trigger": trigger,
        "assertion": assertion,
        "failure_point": failure_point,
        "original_count": original_count,
        "kept_count": kept_count,
        "dropped_count": dropped_count,
        "dropped_breakdown": {
            "non_essential": non_essential_dropped,
            "deduped": deduped,
            "folded_into_preconditions": len(folded),
        },
        "convergence_ratio": round(convergence_ratio, 3),
    }
    result["repro_summary"] = _build_converged_summary(result)

    _log.debug(
        "[minimal_repro] 收敛: %d 步 → 关键 %d 步 + %d 前置（收敛率 %.1f%%）",
        original_count,
        kept_count,
        len(preconditions),
        convergence_ratio * 100,
    )
    return result


def converge_repro_strings(
    repro_steps: list[str],
    max_key_steps: int = DEFAULT_MAX_KEY_STEPS,
    expected: Any = None,
    actual: Any = None,
    error: str = "",
) -> dict[str, Any]:
    """对 Bug 报告回放出的字符串步骤做最小复现收敛。

    ``_bug_report._load_case_steps`` 回放出的是 ``list[str]``（形如
    ``["前置：已登录", "点击 保存按钮", "期望：...", "实际：..."]``），
    没有 action_type/status 结构。本函数按文本前缀识别语义后复用
    :func:`converge_minimal_repro` 的收敛算法。

    Args:
        repro_steps: 字符串步骤列表（可含「前置：」「期望：」「实际：」行）。
        max_key_steps: 关键路径步数上限（默认 3）。
        expected: 显式期望值（缺省时从「期望：」行解析）。
        actual: 显式实际值（缺省时从「实际：」行解析）。
        error: 失败原因文案（写入 assertion.error）。

    Returns:
        与 :func:`converge_minimal_repro` 同构的最小复现清单。
    """
    precondition_lines: list[str] = []
    action_lines: list[str] = []
    _expected = expected
    _actual = actual

    for raw in repro_steps or []:
        text = str(raw or "").strip()
        if not text:
            continue
        if text.startswith("前置：") or text.startswith("前置:"):
            precondition_lines.append(text.split("：", 1)[-1].split(":", 1)[-1].strip())
            continue
        if text.startswith("期望：") or text.startswith("期望:"):
            if _expected is None:
                _expected = text.split("：", 1)[-1].split(":", 1)[-1].strip()
            continue
        if text.startswith("实际：") or text.startswith("实际:"):
            if _actual is None:
                _actual = text.split("：", 1)[-1].split(":", 1)[-1].strip()
            continue
        if text.startswith("（无步骤回放来源"):
            continue
        action_lines.append(text)

    if not action_lines:
        return {
            "schema_version": REPRO_SCHEMA_VERSION,
            "has_repro": False,
            "preconditions": _dedup_preserve_order(precondition_lines),
            "key_steps": [],
            "key_step_count": 0,
            "trigger": None,
            "assertion": {
                "check_type": "-",
                "expected": None if _expected is None else str(_expected),
                "actual": None if _actual is None else str(_actual),
                "error": error or "",
            },
            "failure_point": -1,
            "original_count": len(repro_steps or []),
            "kept_count": 0,
            "dropped_count": 0,
            "dropped_breakdown": {
                "non_essential": 0,
                "deduped": 0,
                "folded_into_preconditions": 0,
            },
            "convergence_ratio": 0.0,
            "repro_summary": "无可回放的操作步骤，无法收敛最小复现路径",
        }

    # 字符串步骤统一视为必需动作，最后一条标记为失败点
    synthetic: list[dict[str, Any]] = []
    for i, text in enumerate(action_lines):
        synthetic.append(
            {
                "action_type": "click" if i < len(action_lines) - 1 else "verify_step",
                "action_desc": text,
                "target": text,
                "status": "passed" if i < len(action_lines) - 1 else "failed",
                "error": error if i == len(action_lines) - 1 else "",
            }
        )

    return converge_minimal_repro(
        synthetic,
        max_key_steps=max_key_steps,
        expected=_expected,
        actual=_actual,
        precondition="; ".join(_dedup_preserve_order(precondition_lines)),
    )


def _build_converged_summary(result: dict[str, Any]) -> str:
    """构建收敛后最小复现清单的人类可读摘要。"""
    lines: list[str] = []
    preconditions = result.get("preconditions") or []
    if preconditions:
        lines.append("前置条件：")
        for cond in preconditions:
            lines.append(f"  - {cond}")
    else:
        lines.append("前置条件：（无，可直接从关键路径复现）")

    lines.append("关键路径（%d 步）：" % result.get("key_step_count", 0))
    for step in result.get("key_steps") or []:
        marker = " ← 失败点" if step.get("is_failure") else ""
        lines.append(f"  {step['seq']}. {step['action_desc']}{marker}")

    assertion = result.get("assertion") or {}
    if assertion.get("expected") is not None or assertion.get("actual") is not None:
        lines.append(
            "断言（%s）：期望「%s」 vs 实际「%s」"
            % (
                assertion.get("check_type") or "-",
                assertion.get("expected") if assertion.get("expected") is not None else "(无)",
                assertion.get("actual") if assertion.get("actual") is not None else "(无)",
            )
        )
    if assertion.get("error"):
        lines.append("失败原因：%s" % assertion["error"])
    return "\n".join(lines)


def build_converged_section(result: dict[str, Any]) -> str:
    """把收敛后的最小复现清单渲染为 Markdown 报告区块。

    Args:
        result: :func:`converge_minimal_repro` / :func:`converge_repro_strings` 的返回值。

    Returns:
        Markdown 格式的最小复现区块字符串。
    """
    if not result.get("has_repro"):
        return "## 最小复现路径（收敛）\n\n%s\n" % result.get(
            "repro_summary", "无失败步骤，无需复现。"
        )

    lines = [
        "## 最小复现路径（收敛）",
        "",
        "> 原始 %d 步 → 关键路径 %d 步 + %d 条前置条件（收敛率 %.1f%%）"
        % (
            result["original_count"],
            result["key_step_count"],
            len(result.get("preconditions") or []),
            result["convergence_ratio"] * 100,
        ),
        "",
        "```",
        result["repro_summary"],
        "```",
        "",
    ]
    return "\n".join(lines)
