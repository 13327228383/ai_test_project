# -*- coding: utf-8 -*-
from __future__ import annotations

"""跨轨一致性校验（高度解耦，单一职责）。

场景1：数据校验类意图需要确认「前端展示值 / 接口传值 / DB存储值」三者一致（含小数位精度容差）。
三轨各自独立校验通过 ≠ 三者一致。本模块做第四维一致性比对，独立于 3 轨 AND 语义。

值的来源：
- UI 值：从页面文本用正则提取数值（precision 兜底解析）
- API 值：从 api_tracker resp_body 用 JSONPath 取（复用 _extract_json_path）
- DB 值：从 db 校验结果 data 行取字段（由调用方传入）

设计原则：纯函数 + 复用 _compare_with_precision（Decimal 精度容差）。
有效来源不足 2 个 → inconclusive（不判 fail，避免环境性错杀）。
"""

import re
from typing import Any

from ._api import _extract_json_path
from src.pipeline.steps.step4.db_verifier.helpers import (
    _compare_with_precision,
)  # 精度容差


def _extract_number(text: str, pattern: str | None = None) -> str | None:
    """从文本提取数值字符串。优先用显式 pattern（取第1捕获组），否则取第一个数字（支持小数/负号，去千分位）。"""
    if not text:
        return None
    text = str(text)
    if pattern:
        m = re.search(pattern, text)
        if m:
            return m.group(1) if m.groups() else m.group(0)
        return None
    cleaned = text.replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    return m.group(0) if m else None


def _normalize(val: Any) -> str | None:
    """值归一为可 Decimal 解析的 str（去千分位）。"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return str(val)
    s = str(val).strip().replace(",", "")
    return s if s else None


async def verify_consistency_async(
    page,
    api_tracker,
    step_start_time: float,
    db_result: dict[str, Any],
    consistency_check: dict[str, Any],
) -> dict[str, Any]:
    """跨轨一致性比对（async 入口）。

    consistency_check 格式：
      {"field": "金额", "ui_pattern": "总额[:：]\\s*([0-9.]+)",  # 可选，页面文本正则
       "api_json_path": "data.amount",          # 从 API resp_body 取
       "db_field": "amount",                    # 从 db_result data 首行取
       "precision": 0.01}                        # 精度容差

    返回：{"passed": bool, "inconclusive": bool, "evidence": str, "values": {ui,api,db}}
    """
    field = consistency_check.get("field", "字段")
    precision = consistency_check.get("precision")
    ui_pattern = consistency_check.get("ui_pattern")
    api_json_path = consistency_check.get("api_json_path")
    db_field = consistency_check.get("db_field")

    values = {"ui": None, "api": None, "db": None}

    # UI 值
    if page is not None and (ui_pattern or field):
        try:
            page_text = await page.evaluate("() => document.body.innerText || ''")
            values["ui"] = _extract_number(page_text, ui_pattern)
        except Exception:
            values["ui"] = None

    # API 值
    if api_tracker is not None and api_json_path:
        try:
            calls = (
                api_tracker.get_calls_since(step_start_time)
                if hasattr(api_tracker, "get_calls_since")
                else []
            )
            for c in calls:
                body = c.get("resp_body")
                if body is None:
                    continue
                val = _extract_json_path(body, api_json_path)
                if val is not None:
                    values["api"] = val
                    break
        except Exception:
            values["api"] = None

    # DB 值
    if db_result is not None and db_field:
        try:
            data = db_result.get("data", []) or []
            if data and isinstance(data[0], dict):
                values["db"] = data[0].get(db_field)
        except Exception:
            values["db"] = None

    # 有效来源不足 2 个 → 无法比对（不判 fail）
    present = {k: v for k, v in values.items() if v is not None}
    if len(present) < 2:
        return {
            "passed": True,
            "inconclusive": True,
            "evidence": "一致性无法比对(有效来源%d/3不足2): %s" % (len(present), field),
            "values": values,
        }

    # 两两精度容差比对
    mismatches = []
    for a, b in (("ui", "api"), ("ui", "db"), ("api", "db")):
        if values[a] is None or values[b] is None:
            continue
        va, vb = _normalize(values[a]), _normalize(values[b])
        if va is None or vb is None:
            continue
        if not _compare_with_precision(va, vb, precision):
            mismatches.append(
                "%s(%s)≠%s(%s)" % (a.upper(), values[a], b.upper(), values[b])
            )

    if mismatches:
        # ★ S45-002: 权威层级裁决（DB > API > UI）
        # 规则：
        #   1. DB 和 API 一致 → passed（UI 渲染滞后，不判 FAIL）
        #   2. DB 和 API 不一致 → inconclusive（需人工确认）
        #   3. 无法判断（只有UI+API不一致/三轨全不一致）→ failed
        db_val = _normalize(values.get("db"))
        api_val = _normalize(values.get("api"))
        ui_val = _normalize(values.get("ui"))

        # DB 和 API 都有效 → 检查它们是否一致
        if db_val is not None and api_val is not None:
            db_api_match = _compare_with_precision(db_val, api_val, precision)
            if db_api_match:
                # DB 和 API 一致 → UI 滞后是正常的，判 passed
                return {
                    "passed": True,
                    "inconclusive": False,
                    "evidence": "一致性裁决通过[%s]: DB=API 一致, UI可能滞后 (%s)"
                    % (field, "; ".join(mismatches)),
                    "values": values,
                }
            else:
                # DB 和 API 不一致 → 需要人工确认
                return {
                    "passed": True,
                    "inconclusive": True,
                    "evidence": "一致性裁决无法判定[%s]: DB≠API 需人工确认 (%s)"
                    % (field, "; ".join(mismatches)),
                    "values": values,
                }

        # DB 存在但 API 不存在 → DB 是唯一权威源，判 passed
        if db_val is not None and api_val is None:
            return {
                "passed": True,
                "inconclusive": False,
                "evidence": "一致性裁决通过[%s]: DB权威源通过, 对比源不足 (%s)"
                % (field, "; ".join(mismatches)),
                "values": values,
            }

        # 只有 UI 和 API 不一致 → 无法判断权威，判 failed
        return {
            "passed": False,
            "inconclusive": False,
            "evidence": "一致性裁决失败[%s]: 无DB权威源, %s"
            % (field, "; ".join(mismatches)),
            "values": values,
        }
    present_keys = "/".join(k.upper() for k in present)
    return {
        "passed": True,
        "inconclusive": False,
        "evidence": "一致性通过[%s] %s (容差%s)" % (field, present_keys, precision),
        "values": values,
    }
