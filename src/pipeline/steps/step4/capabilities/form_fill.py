# -*- coding: utf-8 -*-
"""
标准表单填写与提交能力
======================
检测所有输入框，按 placeholder 推断测试数据后提交，验证成功提示。
仅用于 capability_hint = form_fill_and_submit 类业务意图。
返回 True 表示已成功提交；False 表示回退 AI 路径。
"""

import logging
import re as _re

_log = logging.getLogger(__name__)


async def try_form_fill_and_submit(engine, goal: str) -> bool:
    """
    标准表单填写能力：
    1) 识别所有可见 input/textarea/select；
    2) 按 placeholder/name 推断测试数据；
    3) 提交表单；
    4) 验证成功提示。
    """
    page = engine._page if engine else None
    if page is None:
        return False
    try:
        # ── 1. 识别所有可见输入框 ──
        inputs = await _collect_visible_inputs(page)
        if not inputs:
            _log.info("  [FORM] 标准能力：未找到可填写的输入框")
            return False
        _log.info("  [FORM] 标准能力：发现 %d 个输入框", len(inputs))

        # ── 2. 按 placeholder 推断并填写测试数据 ──
        filled_count = 0
        for inp_info in inputs:
            test_value = _infer_test_value(inp_info, goal)
            if test_value:
                try:
                    loc = inp_info["locator"]
                    await loc.click()
                    await loc.fill("")
                    await loc.fill(test_value)
                    filled_count += 1
                    _log.info(
                        "  [FORM] 标准能力：已填写 %s = %s",
                        inp_info.get("placeholder") or inp_info.get("name", "?")[:30],
                        test_value[:30],
                    )
                except Exception as _e:
                    _log.debug("[form] 填写异常: %s", _e, exc_info=True)
                    continue

        if filled_count == 0:
            _log.info("  [FORM] 标准能力：未能填写任何输入框，回退 AI")
            return False

        # ── 3. 提交表单 ──
        submitted = await _submit_form(page)
        if not submitted:
            _log.info("  [FORM] 标准能力：未找到提交按钮，回退 AI")
            return False

        # ── 4. 验证成功 ──
        # ★ S4-011: 删除冗余 sleep,直接用 wait_for_load_state 确定性等待
        await page.wait_for_timeout(300)  # 让提交事件先触发
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception as _e:
            _log.debug("[form] 非关键异常: %s", _e, exc_info=True)

        if await _verify_submit_success(page):
            _log.info("  [FORM] 标准能力：✓ 表单提交成功")
            return True

        _log.info("  [FORM] 标准能力：未能确认提交成功，回退 AI")
        return False
    except Exception as e:
        _log.info("  [FORM] 标准能力异常，回退 AI: %s", str(e)[:120])
        return False


async def _collect_visible_inputs(page) -> list:
    """收集所有可见的 input/textarea 元素信息。"""
    inputs = []
    try:
        # 收集 input（排除 hidden/submit/button/password）
        loc = page.locator(
            'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="password"]):not([type="file"])'
        )
        count = await loc.count()
        for i in range(min(count, 20)):  # 限制最多 20 个，避免超大表单
            el = loc.nth(i)
            try:
                if await el.is_visible():
                    placeholder = await el.get_attribute("placeholder") or ""
                    name = await el.get_attribute("name") or ""
                    input_type = await el.get_attribute("type") or "text"
                    inputs.append(
                        {
                            "locator": el,
                            "placeholder": placeholder,
                            "name": name,
                            "type": input_type,
                        }
                    )
            except Exception as _e:
                _log.debug("[form] 元素收集异常: %s", _e, exc_info=True)
                continue

        # 收集 textarea
        ta_loc = page.locator("textarea")
        ta_count = await ta_loc.count()
        for i in range(min(ta_count, 10)):
            el = ta_loc.nth(i)
            try:
                if await el.is_visible():
                    placeholder = await el.get_attribute("placeholder") or ""
                    name = await el.get_attribute("name") or ""
                    inputs.append(
                        {
                            "locator": el,
                            "placeholder": placeholder,
                            "name": name,
                            "type": "textarea",
                        }
                    )
            except Exception as _e:
                _log.debug("[form] textarea收集异常: %s", _e, exc_info=True)
                continue
    except Exception as _e:
        _log.debug("[form] 收集异常: %s", _e, exc_info=True)
    return inputs


def _infer_test_value(inp_info: dict, goal: str) -> str:
    """按 placeholder/name 推断测试数据（走 value_generator 统一管道）。

    ★ §13.3 通用修复(2026-07-24 续): Step4 form_fill 能力改用 value_generator，禁硬编码。
      根因: 原 _infer_test_value 硬编码 13800138000/test@example.com 等，违反 §13.3。
      方案: 保留 goal 提取作第一优先级；fallback 走 generate_value_from_type(field_type, field_name)。
      通用性: value_generator 内置手机/邮箱/日期/金额/url 等语义，跨系统/行业通用。
      职责边界: form_fill 属 §12.1 允许的 Self-Healing 兜底，用 value_generator 不越界。
    """
    ph = (inp_info.get("placeholder") or "").lower()
    name = (inp_info.get("name") or "").lower()
    inp_type = inp_info.get("type", "text")

    # 1. 从 goal 提取已有值（如"名称：测试数据"）— 保留，第一优先级
    if goal:
        val_patterns = [
            r'名称["\':：\s]*([^\s，,]+)',
            r'标题["\':：\s]*([^\s，,]+)',
            r'描述["\':：\s]*([^\s，,]+)',
        ]
        for pat in val_patterns:
            m = _re.search(pat, goal)
            if m and m.group(1):
                return m.group(1).strip()

    # 2. 走 value_generator 统一管道（禁硬编码）
    try:
        from src.pipeline.step3.value_generator import generate_value_from_type

        _type_map = {
            "text": "string",
            "textarea": "string",
            "url": "string",
            "number": "integer",
            "email": "string",
            "date": "string",
        }
        _ftype = _type_map.get(inp_type, "string")
        _fname = ph or name or inp_type
        return generate_value_from_type(_ftype, _fname)
    except Exception as _e:
        _log.debug("[form] value_generator 不可用，用兜底: %s", _e, exc_info=True)
        return "测试数据"


async def _submit_form(page) -> bool:
    """定位并点击提交按钮。"""
    submit_selectors = [
        'button:has-text("保存")',
        'button:has-text("提交")',
        'button:has-text("确认")',
        'button:has-text("确定")',
        'button:has-text("Submit")',
        'button:has-text("Save")',
        'button[type="submit"]',
        'input[type="submit"]',
        ".submit-btn",
        ".save-btn",
    ]
    for sel in submit_selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click()
                _log.info("  [FORM] 标准能力：已点击提交按钮 (%s)", sel[:50])
                return True
        except Exception as _e:
            _log.debug("[form] 提交按钮异常: %s", _e, exc_info=True)
            continue
    return False


async def _verify_submit_success(page) -> bool:
    """验证表单提交成功。"""
    try:
        body_text = await page.evaluate("() => document.body.innerText || ''") or ""
    except Exception as _e:
        _log.debug("[form] body文本获取异常: %s", _e, exc_info=True)
        body_text = ""
    success_markers = (
        "保存成功",
        "提交成功",
        "创建成功",
        "添加成功",
        "新增成功",
        "操作成功",
        "成功",
        "success",
        "saved",
        "created",
    )
    if any(m.lower() in body_text.lower() for m in success_markers):
        _log.info("  [FORM] 标准能力：✓ 成功提示命中")
        return True
    error_markers = (
        "保存失败",
        "提交失败",
        "操作失败",
        "错误",
        "失败",
        "error",
        "fail",
    )
    if any(m.lower() in body_text.lower() for m in error_markers):
        _log.info("  [FORM] 标准能力：检测到错误提示，提交失败")
        return False
    # 无成功也无错误提示，检查表单是否清空（提交后通常清空）
    try:
        visible_inputs = page.locator(
            'input:not([type="hidden"]):not([type="submit"]):not([type="button"])'
        )
        if await visible_inputs.count() > 0:
            first_val = await visible_inputs.first.input_value()
            if not first_val:
                _log.info("  [FORM] 标准能力：✓ 表单已清空，假设提交成功")
                return True
    except Exception as _e:
        _log.debug("[form] 表单清空检查异常: %s", _e, exc_info=True)
    return False
