# -*- coding: utf-8 -*-
"""
Intent Validator - P0 防幻觉：参数枚举值确定性校验 (v3.0)
==========================================================
从 checks[].expect 中提取预期值，与 DB schema 已知枚举值比对。

零 AI 调用 — 纯字符串匹配。
"""

import json
import logging

from ._data import load_cross_check_data

_log = logging.getLogger(__name__)


def cross_check_enums(cases: list[dict]) -> list[dict]:
    """
    v3.0 确定性校验：从 checks[].expect 中提取参数预期值，
    与 DB schema 已知枚举值比对。

    返回: [{"case_id": "...", "reason": "...", "severity": "error|warning", "cross_check": "enum"}]
    """
    data = load_cross_check_data()
    db_enums = data.get("db_enums", {})

    if not db_enums:
        return [
            {
                "case_id": "SYSTEM",
                "reason": "⚠️ DB枚举值基准未加载，枚举确定性校验已跳过。建议执行Step1加载DB数据源。",
                "severity": "warning",
                "cross_check": "baseline",
            }
        ]

    errors = []

    for c in cases:
        if not isinstance(c, dict):
            continue
        cid = c.get("case_id", "?")

        checks = c.get("checks") or []
        if isinstance(checks, str):
            try:
                checks = json.loads(checks)
            except Exception:
                checks = []

        for ci, chk in enumerate(checks if isinstance(checks, list) else []):
            if not isinstance(chk, dict):
                continue
            # 只检查 db track 的 exact check — 它们有具体的期望值
            if str(chk.get("track", "")).strip().lower() != "db":
                continue
            if str(chk.get("precision", "")).strip() != "exact":
                continue

            expect = str(chk.get("expect", "")).strip()
            if not expect:
                continue

            # 提取 expect 中的字段名=值 模式
            # 格式如: "status=1, name=active" 或 "count > 0"
            # 优先检查等值判断（=）
            import re

            eq_pairs = re.findall(r'(\w+)\s*=\s*[\'"]?([^\'",;\s]+)', expect)
            for field_name, field_value in eq_pairs:
                if not field_value or field_value.isdigit():
                    continue
                field_value_lower = field_value.lower().strip("'\" ")

                # 在所有已知枚举中查找该字段
                for enum_key, enum_vals in db_enums.items():
                    # enum_key 格式: "table.column"
                    if field_name.lower() == enum_key.split(".")[-1].lower():
                        if field_value_lower not in {str(v).lower() for v in enum_vals}:
                            known = sorted(enum_vals)[:10]
                            errors.append(
                                {
                                    "case_id": cid,
                                    "reason": (
                                        f"checks[{ci}].expect中 {field_name}={field_value} "
                                        f"不在{enum_key}的已知枚举值内（已知: {', '.join(str(v) for v in known)}）。"
                                        f"请使用项目数据字典中的真实枚举值。"
                                    ),
                                    "severity": "error",  # ★ 编造枚举值 → 阻断级
                                    "cross_check": "enum",
                                }
                            )

    return errors
