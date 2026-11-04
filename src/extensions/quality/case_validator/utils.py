# -*- coding: utf-8 -*-
"""
Intent Validator - 通用工具函数 (v3.0)
"""

import json


def get_intent_summary(intent: dict) -> str:
    """
    提取测试用例摘要文本，用于安全关键词扫描和交叉校验。

    v3.0: 从 title + actions + checks 收集文本，不再依赖废弃的 verification_plan。
    """
    parts = [
        str(intent.get("title", "")),
        str(intent.get("case_id", "")),
        str(intent.get("type", "")),
    ]

    # 收集 actions 中的操作描述
    actions = intent.get("actions") or []
    if isinstance(actions, str):
        try:
            actions = json.loads(actions)
        except Exception:
            actions = []
    if isinstance(actions, list):
        for act in actions:
            if isinstance(act, dict):
                parts.append(str(act.get("type", "")))
                parts.append(str(act.get("target_hint", "")))

    # 收集 checks 中的检查内容和预期
    checks = intent.get("checks") or []
    if isinstance(checks, str):
        try:
            checks = json.loads(checks)
        except Exception:
            checks = []
    if isinstance(checks, list):
        for chk in checks:
            if isinstance(chk, dict):
                check_data = chk.get("check", "")
                if isinstance(check_data, dict):
                    parts.append(json.dumps(check_data, ensure_ascii=False))
                else:
                    parts.append(str(check_data))
                parts.append(str(chk.get("expect", "")))

    return " ".join(str(p) for p in parts if p)
