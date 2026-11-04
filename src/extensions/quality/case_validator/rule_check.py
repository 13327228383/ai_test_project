# -*- coding: utf-8 -*-
"""
Intent Validator - 快速规则校验（纯代码，不调 AI）
"""

import json

from .constants import (
    REQUIRED_FIELDS,
    VALID_TYPES,
    VALID_PRIORITIES,
    INTENT_CORE_FIELDS,
    SECURITY_KEYWORDS,
)
from .result import ValidationResult
from .utils import get_intent_summary


def quick_rule_check(intents: list[dict]) -> ValidationResult:
    """快速规则校验（纯代码，不调 AI）。"""
    result = ValidationResult()
    result.total_cases = len(intents)

    has_security_intent = False
    need_security = False

    for i, c in enumerate(intents):
        if not isinstance(c, dict):
            result.errors.append(
                {
                    "index": i,
                    "case_id": "?",
                    "reason": "元素不是有效 JSON 对象",
                    "severity": "error",
                }
            )
            result.failed_intents += 1
            continue

        cid = c.get("case_id", f"#{i+1}")
        intent_ok = True

        # 1. 必填字段
        for f in REQUIRED_FIELDS:
            if not c.get(f):
                result.errors.append(
                    {
                        "case_id": cid,
                        "reason": f"缺少必填字段: {f}",
                        "severity": "error",
                    }
                )
                intent_ok = False

        # 2. type 校验
        ct = c.get("type", "")
        if ct and ct not in VALID_TYPES:
            result.warnings.append(
                {
                    "case_id": cid,
                    "message": f"测试用例类型 '{ct}' 不在标准六类中",
                }
            )

        # 3. priority 校验
        cp = c.get("priority", "")
        if cp and cp not in VALID_PRIORITIES:
            result.warnings.append(
                {
                    "case_id": cid,
                    "message": f"测试用例等级 '{cp}' 不在标准范围",
                }
            )

        # 4. v9 核心字段完整性校验（title 替代 intent_schema）
        missing_core = [
            f for f in INTENT_CORE_FIELDS if not str(c.get(f) or "").strip()
        ]
        if missing_core:
            result.errors.append(
                {
                    "case_id": cid,
                    "reason": f"v9 核心字段缺失: {', '.join(missing_core)}",
                    "severity": "error",
                }
            )
            intent_ok = False

        # 5. checks 三轨证据校验 (v3.0: 从 checks[] 读取)
        checks = c.get("checks") or []
        if isinstance(checks, str):
            try:
                checks = json.loads(checks)
            except Exception:
                checks = []
        if isinstance(checks, list):
            if len(checks) == 0:
                result.warnings.append(
                    {
                        "case_id": cid,
                        "message": "checks 为空，建议至少 1 个检查点",
                    }
                )
        else:
            result.errors.append(
                {
                    "case_id": cid,
                    "reason": "checks 不是有效数组",
                    "severity": "error",
                }
            )
            intent_ok = False

        # ★ P5 (2026-07-07): navigate target_hint 格式校验
        # 禁止：章节标题、图片描述、编造 URL
        # ★ 通用修复 (2026-07-18): 允许中文页面名作为 target_hint。
        #   Step4 的 navigate handler 支持菜单导航回退，可处理中文页面名。
        #   Prompt 规则：无URL时使用页面名称，Step4通过菜单自动导航。
        actions = c.get("actions") or []
        if isinstance(actions, str):
            try:
                actions = json.loads(actions)
            except Exception:
                actions = []
        if isinstance(actions, list):
            import re as _re

            for ai_idx, act in enumerate(actions):
                if not isinstance(act, dict):
                    continue
                if act.get("type") != "navigate":
                    continue
                nav_target = str(act.get("target_hint", "") or "").strip()
                if not nav_target:
                    result.errors.append(
                        {
                            "case_id": cid,
                            "reason": f"navigate action[{ai_idx}] 缺少 target_hint",
                            "severity": "error",
                        }
                    )
                    intent_ok = False
                    continue
                # ★ 通用修复 (2026-07-18): 中文页面名是合法的 target_hint。
                #   Step4 会通过菜单导航回退处理。降级为 warning 而非 error。
                _is_chinese_page = bool(_re.search(r"[\u4e00-\u9fff]", nav_target))
                if _is_chinese_page:
                    # 中文页面名 → 允许，但需检查不是章节标题或图片描述
                    if (
                        "图:" in nav_target
                        or _re.search(r"image\d{4}", nav_target)
                        or nav_target.endswith(".png")
                        or nav_target.endswith(".jpg")
                    ):
                        result.errors.append(
                            {
                                "case_id": cid,
                                "reason": f"navigate target_hint '{nav_target}' 是图片描述/文件名，禁止作为 URL",
                                "severity": "error",
                            }
                        )
                        intent_ok = False
                        continue
                    if _re.match(r"^\d+\.", nav_target):
                        result.errors.append(
                            {
                                "case_id": cid,
                                "reason": f"navigate target_hint '{nav_target}' 是需求章节标题，禁止作为 URL",
                                "severity": "error",
                            }
                        )
                        intent_ok = False
                        continue
                    # 中文页面名 → 合法（Step4 菜单导航回退）
                    result.warnings.append(
                        {
                            "case_id": cid,
                            "message": f"navigate target_hint '{nav_target}' 为中文页面名，Step4 将通过菜单导航",
                        }
                    )
                    continue
                # 规则1：必须以 / 开头（英文路径）
                if not nav_target.startswith("/"):
                    result.errors.append(
                        {
                            "case_id": cid,
                            "reason": f"navigate target_hint '{nav_target}' 必须以 / 开头",
                            "severity": "error",
                        }
                    )
                    intent_ok = False
                    continue
                # 规则3：禁止图片描述/文件名（如 图: image2026-5-22_15-0-5.png）
                if (
                    "图:" in nav_target
                    or _re.search(r"image\d{4}", nav_target)
                    or nav_target.endswith(".png")
                    or nav_target.endswith(".jpg")
                ):
                    result.errors.append(
                        {
                            "case_id": cid,
                            "reason": f"navigate target_hint '{nav_target}' 是图片描述/文件名，禁止作为 URL",
                            "severity": "error",
                        }
                    )
                    intent_ok = False
                    continue
                # 规则4：禁止章节标题特征（如 /03.角色权限管理-随版本更新）
                # 形如 /数字. 后跟文字
                if _re.match(r"^/\d+\.", nav_target):
                    result.errors.append(
                        {
                            "case_id": cid,
                            "reason": f"navigate target_hint '{nav_target}' 是需求章节标题，禁止作为 URL",
                            "severity": "error",
                        }
                    )
                    intent_ok = False
                    continue

        # 6. 安全测试用例检测
        if ct == "安全":
            has_security_intent = True

        # ★ 通用修复 (2026-07-18): 空动作/空检查验证
        # 防止 AI 生成 text=? selector=? 或 check=? expect=? 等无效占位符
        _empty_actions = []
        for ai_idx, act in enumerate(actions):
            if not isinstance(act, dict):
                continue
            act_type = act.get("type", "")
            if act_type == "click":
                _text = str(act.get("text", "") or "").strip()
                _selector = str(act.get("selector", "") or "").strip()
                if (not _text or _text == "?") and (not _selector or _selector == "?"):
                    _empty_actions.append(
                        f"click[{ai_idx}]: text/selector 均为空或占位符 '?'"
                    )
            elif act_type == "fill":
                _hint = str(act.get("target_hint", "") or "").strip()
                _val = str(act.get("value", "") or "").strip()
                if (not _hint or _hint == "?") and (not _val or _val == "?"):
                    _empty_actions.append(
                        f"fill[{ai_idx}]: target_hint/value 均为空或占位符 '?'"
                    )
        if _empty_actions:
            result.errors.append(
                {
                    "case_id": cid,
                    "reason": f"空动作: {'; '.join(_empty_actions)}",
                    "severity": "error",
                }
            )
            intent_ok = False

        # 空检查验证
        _empty_checks = []
        for ci_idx, chk in enumerate(checks):
            if not isinstance(chk, dict):
                continue
            _check = str(chk.get("check", "") or "").strip()
            _expect = str(chk.get("expect", "") or "").strip()
            if (not _check or _check == "?") and (not _expect or _expect == "?"):
                _empty_checks.append(
                    f"checks[{ci_idx}]: check/expect 均为空或占位符 '?'"
                )
            elif _check == "?":
                _empty_checks.append(f"checks[{ci_idx}]: check 为占位符 '?'")
            elif _expect == "?":
                _empty_checks.append(f"checks[{ci_idx}]: expect 为占位符 '?'")
        if _empty_checks:
            result.errors.append(
                {
                    "case_id": cid,
                    "reason": f"空检查: {'; '.join(_empty_checks)}",
                    "severity": "error",
                }
            )
            intent_ok = False

        # ★  根因修复: check 字段（expect + check.expected）必须短且具体
        #   根因: AI/规则生成的长句 expect（如"商品管理、订单处理、供应商管理、库存管理等核心功能正常可用"）
        #   在 Step 4 的 text_contains 校验中无法匹配页面元素，导致用例假 FAIL。
        #   修复: 在规则校验层拦截长句/多值 expect 和 check.expected，强制 AI/规则重试生成短词。
        #   通用性: 适用于所有生成用例的场景，不依赖具体页面/系统/行业。
        _long_expects = []
        for ci_idx, chk in enumerate(checks):
            if not isinstance(chk, dict):
                continue
            # 检查1: expect 字段（描述标签）不能含中文顿号且不能超长
            _expect = str(chk.get("expect", "") or "").strip()
            if "、" in _expect:
                _long_expects.append(
                    f"checks[{ci_idx}]: expect 含中文顿号'、'（{_expect[:40]}）"
                )
                continue
            if len(_expect) > 30:
                _long_expects.append(
                    f"checks[{ci_idx}]: expect 过长({len(_expect)}字)（{_expect[:40]}...）"
                )
                continue
            # 检查2: check.expected 字段（text_contains_check 的实际搜索文本）不能是长句
            _check_obj = chk.get("check", {})
            if isinstance(_check_obj, dict) and _check_obj.get("type") == "text_contains_check":
                _chk_expected = str(_check_obj.get("expected", "") or "").strip()
                if "、" in _chk_expected:
                    _long_expects.append(
                        f"checks[{ci_idx}].check.expected 含中文顿号'、'（{_chk_expected[:40]}）"
                    )
                    continue
                if len(_chk_expected) > 30:
                    _long_expects.append(
                        f"checks[{ci_idx}].check.expected 过长({len(_chk_expected)}字)（{_chk_expected[:40]}...）"
                    )
        if _long_expects:
            result.errors.append(
                {
                    "case_id": cid,
                    "reason": f"check 值不符合规范: {'; '.join(_long_expects[:3])}",
                    "severity": "error",
                }
            )
            intent_ok = False

        # 7. 标题中的具体操作描述检测
        #   根因修复(2026-08-25): 原规则用 "payload" 泛词一刀切，误伤合法安全用例标题
        #   （如"验证XSS payload被转义"，安全用例本就合理使用 payload 一词）。
        #   修复: "payload" 泛词仅拦截非安全用例；安全用例(type="安全")不受此限。
        title = c.get("title", "")
        _title_has_payload = "payload" in str(title).lower()
        if title and ("步骤" in title or (_title_has_payload and ct != "安全")):
            result.errors.append(
                {
                    "case_id": cid,
                    "reason": "标题包含具体操作描述或 payload",
                    "severity": "error",
                }
            )
            intent_ok = False

        if intent_ok:
            result.passed_intents += 1
        else:
            result.failed_intents += 1

    # 安全测试用例检查
    if not has_security_intent:
        # v3.0: 扫描所有用例的 title + checks + actions 文本，判断是否需要安全意图
        all_text = " ".join(
            str(get_intent_summary(c)) for c in intents if isinstance(c, dict)
        )
        for kw in SECURITY_KEYWORDS:
            if kw in all_text:
                need_security = True
                break
        if need_security:
            result.warnings.append(
                {
                    "case_id": "*",
                    "message": "功能涉及用户输入但缺少安全类型测试用例（SQL注入/XSS/超长字符串）",
                }
            )

    result.passed = result.failed_intents == 0
    if result.passed:
        result.summary = f"全部 {result.total_cases} 条测试用例通过规则校验"
    else:
        result.summary = (
            f"{result.failed_intents}/{result.total_cases} 条测试用例存在错误"
        )

    return result
