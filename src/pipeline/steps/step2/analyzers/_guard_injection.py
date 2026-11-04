"""Step2 闭环反馈 + 幻觉护栏规则注入。

在 AI 从切片中提取三元组之前，注入两类上下文：
  1. 闭环反馈：该模块/切片的历史失败率和失败模式（来自 hallucination_logger）
  2. 幻觉护栏：test_rules 表中自动生成的防幻觉规则（来自 analyze_and_update_rules）

两类注入都非阻断：获取失败时静默跳过，不影响 AI 调用。
"""

import json
from src.core.guarded import guard
import logging
from typing import Any

from .constants import config

_log = logging.getLogger(__name__)


def build_step2_guard_injection(chunk: dict[str, Any]) -> str:
    """构建 Step2 AI 提取时的护栏注入（历史失败反馈 + 幻觉防避规则）。"""
    parts: list[str] = []

    # ── 1. 闭环反馈：历史失败率 + 失败模式 ──
    with guard("build_step2_guard_injection: from src.extensions.quality.hallucin", log=_log):
        from src.extensions.quality.hallucination_logger import (
            generate_failure_feedback_prompt,
        )

        _section = (chunk.get("section") or "").strip()
        _module = _section[:80] if _section else None
        _feedback = generate_failure_feedback_prompt(
            module=_module,
            project_name=getattr(config, "project_name", None),
        )
        if _feedback:
            parts.append(_feedback)

    # ── 2. 幻觉护栏规则：从 test_rules 表查询自动生成的防幻觉规则 ──
    with guard("build_step2_guard_injection: _rules_text = _query_hallucination_g", log=_log):
        _rules_text = _query_hallucination_guard_rules()
        if _rules_text:
            parts.append(_rules_text)

    if parts:
        return "\n\n" + "\n\n".join(parts)
    return ""


def _query_hallucination_guard_rules() -> str:
    """从 test_rules 表查询已自动生成的防幻觉护栏规则。

    test_rules 表结构: (scope_type, scope_id, rules_json)
    rules_json 内 patterns 列表中每条含: module, suggested_rule, pattern_id, status
    返回格式化为 Prompt 注入文本，空结果返回空字符串。
    """
    conn = None
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        # 查询全局 + 项目级规则（项目级覆盖全局）
        _proj = getattr(config, "project_name", "") or ""
        rows = conn.execute(
            "SELECT scope_type, rules_json FROM test_rules "
            "WHERE (scope_type='global' AND scope_id='') OR (scope_type='project' AND scope_id=%s) "
            "ORDER BY CASE WHEN scope_type='global' THEN 0 ELSE 1 END",
            (_proj,),
        ).fetchall()

        if not rows:
            return ""

        _all_patterns: list[dict] = []
        for row in rows:
            _rules_json = row.get("rules_json")
            if isinstance(_rules_json, str):
                _rules_json = json.loads(_rules_json)
            if not isinstance(_rules_json, dict):
                continue
            _patterns = _rules_json.get("patterns", [])
            if isinstance(_patterns, list):
                _all_patterns.extend(_patterns)

        if not _all_patterns:
            return ""

        # 去重 + 限制最多 5 条，避免 prompt 过长
        _seen: set[str] = set()
        _unique: list[dict] = []
        for p in _all_patterns:
            _rule = (p.get("suggested_rule") or "").strip()
            if _rule and _rule not in _seen:
                _seen.add(_rule)
                _unique.append(p)
                if len(_unique) >= 5:
                    break

        _lines = ["## ⚠️ 防幻觉护栏规则（自动生成，基于历史执行失败模式）"]
        for p in _unique:
            _rule = (p.get("suggested_rule") or "").strip()
            _module = (p.get("module") or "").strip()
            if _rule:
                _lines.append(f"- [{_module}] {_rule}" if _module else f"- {_rule}")
        return "\n".join(_lines)
    except Exception as _e:
        _log.debug(
            "[guard_injection] test_rules 查询失败(表可能不存在): %s", str(_e)[:80]
        )
        return ""
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.warning("非关键异常: %s", _e, exc_info=True)
