# -*- coding: utf-8 -*-
"""
标准表格导出能力
================
识别表格附近的 Excel/PDF/CSV 导出按钮，点击并验证下载。
仅用于 capability_hint = table_export 类业务意图。
返回 True 表示已成功导出；False 表示回退 AI 路径。
"""

import asyncio
import logging

_log = logging.getLogger(__name__)


async def try_table_export(engine, goal: str) -> bool:
    """
    标准表格导出能力：
    1) 定位表格元素，在其附近找导出按钮；
    2) 点击导出按钮；
    3) 处理格式选择弹窗（若有）；
    4) 验证下载成功。
    """
    page = engine._page if engine else None
    if page is None:
        return False
    try:
        # ── 1. 定位表格附近的导出按钮 ──
        export_btn = await _locate_table_export_button(page, goal)
        if export_btn is None:
            _log.info("  [TABLEX] 标准能力：未找到表格导出按钮，回退 AI")
            return False

        # ── 2. 点击导出按钮 ──
        await export_btn.click()
        _log.info("  [TABLEX] 标准能力：已点击导出按钮")
        await asyncio.sleep(1.0)

        # ── 3. 处理格式选择弹窗 ──
        await _handle_format_dialog(page, goal)

        # ── 4. 验证下载成功 ──
        await asyncio.sleep(2.0)
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception as _e:
            _log.debug("[tablex] 非关键异常: %s", _e, exc_info=True)

        if await _verify_export_success(page):
            _log.info("  [TABLEX] 标准能力：✓ 导出成功")
            return True

        _log.info("  [TABLEX] 标准能力：未能确认导出成功，回退 AI")
        return False
    except Exception as e:
        _log.info("  [TABLEX] 标准能力异常，回退 AI: %s", str(e)[:120])
        return False


async def _locate_table_export_button(page, goal: str):
    """定位表格附近的导出按钮。"""
    # 从 goal 推断导出格式
    if "excel" in goal.lower() or "xlsx" in goal.lower() or "xls" in goal.lower():
        format_keywords = ["Excel", "xlsx", "xls"]
    elif "csv" in goal.lower():
        format_keywords = ["CSV", "csv"]
    elif "pdf" in goal.lower():
        format_keywords = ["PDF", "pdf"]
    else:
        format_keywords = []

    # 策略1：在表格附近找导出按钮
    table_containers = [
        '[role="table"]',
        '[role="grid"]',
        ".table-wrapper",
        ".table-container",
        '[class*="table"]',
    ]
    for container in table_containers:
        try:
            containers = page.locator(container)
            count = await containers.count()
            for i in range(min(count, 5)):
                tbl = containers.nth(i)
                # 先找格式特定按钮
                for kw in format_keywords:
                    btn = tbl.locator(
                        'button:has-text("%s"), a:has-text("%s")' % (kw, kw)
                    )
                    if await btn.count() > 0:
                        return btn.first
                # 再找通用导出按钮
                export_btn = tbl.locator(
                    'button:has-text("导出"), button:has-text("下载"), '
                    'a:has-text("导出"), a:has-text("下载"), '
                    '[class*="export"], [class*="download"]'
                )
                if await export_btn.count() > 0:
                    return export_btn.first
        except Exception as _e:
            _log.debug("[tablex] 表格容器查找异常: %s", _e, exc_info=True)
            continue

    # 策略2：全局找导出按钮（靠近表格的工具栏）
    export_selectors = [
        'button:has-text("导出")',
        'button:has-text("下载")',
        'a:has-text("导出")',
        'a:has-text("下载")',
        '[class*="export"]',
        '[class*="download"]',
        '.toolbar button:has-text("导出")',
        '.toolbar a:has-text("导出")',
    ]
    for sel in export_selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                first = loc.first
                if await first.is_visible():
                    return first
        except Exception as _e:
            _log.debug("[tablex] 全局导出按钮异常: %s", _e, exc_info=True)
            continue
    return None


async def _handle_format_dialog(page, goal: str):
    """处理格式选择弹窗。"""
    # 如果 goal 指定了格式，选择对应格式
    if "excel" in goal.lower():
        format_selectors = [
            'button:has-text("Excel")',
            'button:has-text("excel")',
            'a:has-text("Excel")',
            'li:has-text("Excel")',
        ]
    elif "csv" in goal.lower():
        format_selectors = [
            'button:has-text("CSV")',
            'a:has-text("CSV")',
            'li:has-text("CSV")',
        ]
    elif "pdf" in goal.lower():
        format_selectors = [
            'button:has-text("PDF")',
            'a:has-text("PDF")',
            'li:has-text("PDF")',
        ]
    else:
        format_selectors = [
            'button:has-text("Excel")',
            'button:has-text("确定")',
            'button:has-text("确认")',
            'button:has-text("OK")',
        ]

    for sel in format_selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                if await loc.first.is_visible():
                    await loc.first.click()
                    _log.info("  [TABLEX] 标准能力：已选择格式/确认")
                    await asyncio.sleep(1.0)
                    return
        except Exception as _e:
            _log.debug("[tablex] 格式选择异常: %s", _e, exc_info=True)
            continue


async def _verify_export_success(page) -> bool:
    """验证导出成功。"""
    try:
        body_text = await page.evaluate("() => document.body.innerText || ''") or ""
    except Exception as _e:
        _log.debug("[tablex] body检查异常: %s", _e, exc_info=True)
        body_text = ""

    success_markers = (
        "导出成功",
        "下载成功",
        "已开始下载",
        "导出完成",
        "下载完成",
        "export success",
        "download success",
        "downloading",
    )
    if any(m.lower() in body_text.lower() for m in success_markers):
        _log.info("  [TABLEX] 标准能力：✓ 成功提示命中")
        return True

    # 检查是否有下载请求触发
    try:
        api_calls = await page.evaluate(
            """
            () => {
                const entries = performance.getEntriesByType('resource') || [];
                return entries.filter(e => e.initiatorType === 'download' ||
                    (e.responseEnd > 0 && e.name.match(/\\.(csv|xlsx|xls|pdf|json)$/i)))
                    .slice(-3).map(e => e.name);
            }
        """
        )
        if api_calls and len(api_calls) > 0:
            _log.info("  [TABLEX] 标准能力：✓ 检测到下载请求")
            return True
    except Exception as _e:
        _log.debug("[tablex] 下载检测异常: %s", _e, exc_info=True)

    # 无错误提示也算成功（很多系统导出后静默下载）
    error_markers = ("导出失败", "下载失败", "错误", "失败", "error", "fail")
    if not any(m.lower() in body_text.lower() for m in error_markers):
        _log.info("  [TABLEX] 标准能力：✓ 无错误提示，假设导出成功")
        return True

    return False
