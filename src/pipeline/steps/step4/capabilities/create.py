# -*- coding: utf-8 -*-
"""
标准新增/创建业务记录能力
"""

import logging
from datetime import datetime

_log = logging.getLogger(__name__)


async def try_create_business_record(engine, goal: str) -> bool:
    """
    标准新增/创建业务记录能力：
    1) 找明确的"新增/创建/添加"入口；
    2) 进入表单后只填写空白、可见、非密码的安全字段；
    3) 点击"保存/提交/确定"；
    4) 通过成功提示、列表行/URL/API 变化做证据判断。

    这是能力骨架，不替代复杂业务编排。任一关键证据不足时返回 False，交给 AI 路径继续处理。
    """
    page = engine._page if engine else None
    if page is None:
        return False
    try:
        _log.info(
            "  [CREATE] 标准能力：尝试创建业务记录 goal='%s'", str(goal or "")[:100]
        )

        # ── 1. 记录操作前证据 ──
        before_url = page.url or ""
        before_text = ""
        before_rows = 0
        try:
            before_text = (
                await page.evaluate("() => document.body.innerText || ''") or ""
            )
        except Exception as _e:
            _log.debug(
                "[create] try_create_business_record 异常: %s", _e, exc_info=True
            )
            before_text = ""
        try:
            before_rows = await page.locator(
                '[role="row"], table tbody tr, [role="listitem"], '
                '[class*="table"] [class*="row"], [class*="list"] [class*="item"]'
            ).count()
        except Exception as _e:
            _log.debug(
                "[create] try_create_business_record 异常: %s", _e, exc_info=True
            )
            before_rows = 0

        # ── 2. 点击新增/创建入口 ──
        create_entry_selectors = [
            'button:has-text("新增")',
            'button:has-text("新建")',
            'button:has-text("创建")',
            'button:has-text("添加")',
            'button:has-text("Add")',
            'button:has-text("Create")',
            'a:has-text("新增")',
            'a:has-text("新建")',
            'a:has-text("创建")',
            '[role="button"]:has-text("新增")',
            '[role="button"]:has-text("新建")',
            '[role="button"]:has-text("创建")',
            ".add-btn",
            ".create-btn",
            '[class*="add"]',
            '[class*="create"]',
        ]
        clicked_entry = False
        for sel in create_entry_selectors:
            loc = page.locator(sel)
            if await loc.count() > 0:
                try:
                    await loc.first.click()
                    clicked_entry = True
                    _log.info("  [CREATE] 标准能力：已点击创建入口 (%s)", sel[:60])
                    break
                except Exception as _e:
                    _log.debug("[create] 非关键异常: %s", _e, exc_info=True)
                    continue
        if not clicked_entry:
            _log.info("  [CREATE] 标准能力：未找到明确创建入口，回退 AI")
            return False

        # ★ S4-011: 删除冗余 sleep,直接用 wait_for_load_state 确定性等待
        await page.wait_for_timeout(300)  # 让创建按钮事件先触发
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception as _e:
            _log.debug("[create] 非关键异常: %s", _e, exc_info=True)

        # ── 3. 只填写安全空字段 ──
        import time as _time

        suffix = datetime.now().strftime("%m%d%H%M%S")
        default_text = "自动化测试记录%s" % suffix
        filled_count = 0
        fill_selectors = [
            'input:not([type="hidden"]):not([type="password"]):not([disabled])',
            "textarea:not([disabled])",
        ]
        fields = []
        for sel in fill_selectors:
            try:
                loc = page.locator(sel)
                count = min(await loc.count(), 8)
                for i in range(count):
                    fields.append(loc.nth(i))
            except Exception as _e:
                _log.debug("[create] 非关键异常: %s", _e, exc_info=True)
                continue
        for idx, field in enumerate(fields):
            try:
                visible = await field.is_visible()
                enabled = await field.is_enabled()
                current = await field.input_value(timeout=800)
                if not visible or not enabled or str(current or "").strip():
                    continue
                placeholder = ""
                name = ""
                try:
                    placeholder = await field.get_attribute("placeholder") or ""
                except Exception as _e:
                    _log.debug("[create] 非关键异常: %s", _e, exc_info=True)
                    placeholder = ""
                try:
                    name = await field.get_attribute("name") or ""
                except Exception as _e:
                    _log.debug("[create] 非关键异常: %s", _e, exc_info=True)
                    name = ""
                hint = ("%s %s" % (placeholder, name)).lower()
                if any(
                    k in hint
                    for k in ("password", "密码", "token", "secret", "验证码", "code")
                ):
                    continue
                value = default_text
                if any(k in hint for k in ("手机号", "电话", "phone", "mobile")):
                    value = "138%08d" % (int(_time.time()) % 100000000)
                elif any(k in hint for k in ("邮箱", "email", "mail")):
                    # ★ P2: 演示邮箱后缀走 constants (config.demo.sample_user_email_suffix)
                    try:
                        from src.core.constants import DEMO_EMAIL_SUFFIX

                        email_suffix = DEMO_EMAIL_SUFFIX
                    except Exception as _e:
                        _log.debug("[create] 非关键异常: %s", _e, exc_info=True)
                        email_suffix = "@example.com"
                    value = "auto%s%s" % (suffix, email_suffix)
                elif any(
                    k in hint
                    for k in (
                        "数量",
                        "金额",
                        "价格",
                        "排序",
                        "number",
                        "amount",
                        "price",
                        "sort",
                    )
                ):
                    value = "1"
                elif any(k in hint for k in ("编码", "编号", "code", "no")):
                    value = "AUTO%s" % suffix
                elif any(k in hint for k in ("名称", "标题", "name", "title")):
                    value = default_text
                await field.fill(value)
                filled_count += 1
                _log.info(
                    "  [CREATE] 标准能力：填写字段 %d = %s", filled_count, value[:30]
                )
                if filled_count >= 4:
                    break
            except Exception as _e:
                _log.debug("[create] 非关键异常: %s", _e, exc_info=True)
                continue
        if filled_count <= 0:
            _log.info("  [CREATE] 标准能力：未填写任何安全字段，回退 AI")
            return False

        # ── 4. 选择第一个可用下拉项（若存在） ──
        try:
            selects = page.locator("select:not([disabled])")
            for i in range(min(await selects.count(), 3)):
                sel = selects.nth(i)
                try:
                    options = sel.locator("option")
                    if await options.count() > 1:
                        value = await options.nth(1).get_attribute("value")
                        if value:
                            await sel.select_option(value=value)
                            _log.info("  [CREATE] 标准能力：选择下拉项 value=%s", value)
                except Exception as _e:
                    _log.debug("[create] 非关键异常: %s", _e, exc_info=True)
                    continue
        except Exception as _e:
            _log.debug("[create] 非关键异常: %s", _e, exc_info=True)

        # ── 5. 点击保存/提交按钮 ──
        submit_selectors = [
            'button:has-text("保存")',
            'button:has-text("提交")',
            'button:has-text("确定")',
            'button:has-text("确认")',
            'button:has-text("完成")',
            'button:has-text("Save")',
            'button:has-text("Submit")',
            'button:has-text("OK")',
            'button[type="submit"]',
            '[role="button"]:has-text("保存")',
            '[role="button"]:has-text("提交")',
            '[role="button"]:has-text("确定")',
        ]
        clicked_submit = False
        for sel in submit_selectors:
            loc = page.locator(sel)
            if await loc.count() > 0:
                try:
                    await loc.first.click()
                    clicked_submit = True
                    _log.info("  [CREATE] 标准能力：已点击提交按钮 (%s)", sel[:60])
                    break
                except Exception as _e:
                    _log.debug("[create] 非关键异常: %s", _e, exc_info=True)
                    continue
        if not clicked_submit:
            _log.info("  [CREATE] 标准能力：未找到保存/提交按钮，回退 AI")
            return False

        # ★ S4-011: 删除冗余 sleep,直接用 wait_for_load_state 确定性等待
        await page.wait_for_timeout(300)  # 让保存事件先触发
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception as _e:
            _log.debug("[create] 非关键异常: %s", _e, exc_info=True)

        # ── 6. 创建结果证据判断 ──
        after_url = page.url or ""
        after_text = ""
        try:
            after_text = (
                await page.evaluate("() => document.body.innerText || ''") or ""
            )
        except Exception as _e:
            _log.debug("[create] 非关键异常: %s", _e, exc_info=True)
            after_text = ""
        success_markers = (
            "保存成功",
            "创建成功",
            "新增成功",
            "添加成功",
            "提交成功",
            "操作成功",
            "成功",
            "已保存",
            "已创建",
            "success",
            "saved",
            "created",
        )
        if any(m.lower() in after_text.lower() for m in success_markers):
            _log.info("  [CREATE] 标准能力：✓ 成功提示命中")
            return True

        try:
            after_rows = await page.locator(
                '[role="row"], table tbody tr, [role="listitem"], '
                '[class*="table"] [class*="row"], [class*="list"] [class*="item"]'
            ).count()
            if before_rows > 0 and after_rows > before_rows:
                _log.info(
                    "  [CREATE] 标准能力：✓ 列表行数增加 %d → %d",
                    before_rows,
                    after_rows,
                )
                return True
        except Exception as _e:
            _log.debug("[create] 非关键异常: %s", _e, exc_info=True)

        if after_url != before_url and not any(
            k in after_text for k in ("必填", "不能为空", "错误", "失败")
        ):
            _log.info("  [CREATE] 标准能力：✓ URL 变化且无明显错误")
            return True

        if default_text in after_text:
            _log.info("  [CREATE] 标准能力：✓ 页面出现新建记录名称")
            return True

        _log.info("  [CREATE] 标准能力：未确认创建成功，回退 AI")
        return False
    except Exception as e:
        _log.info("  [CREATE] 标准能力异常，回退 AI: %s", str(e)[:120])
        return False
