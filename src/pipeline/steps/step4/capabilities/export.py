# -*- coding: utf-8 -*-
"""
标准导出数据能力
"""

import logging

_log = logging.getLogger(__name__)


async def try_export_data(engine, goal: str) -> bool:
    """
    标准导出数据能力：
    1) 找"导出/下载/Export/Download"按钮；
    2) 点击后等待文件下载或弹窗；
    3) 验证导出成功提示或文件出现。

    仅用于 capability_hint = export_data 类业务意图。
    返回 True 表示导出成功；False 表示回退 AI 路径。
    """
    page = engine._page if engine else None
    if page is None:
        return False
    try:
        # ── 1. 定位导出按钮 ──
        export_selectors = [
            'button:has-text("导出")',
            'button:has-text("下载")',
            'button:has-text("Export")',
            'button:has-text("Download")',
            'a:has-text("导出")',
            'a:has-text("下载")',
            'a:has-text("Export")',
            '[class*="export"]',
            '[class*="download"]',
            ".export-btn",
            ".download-btn",
        ]
        clicked = False
        for sel in export_selectors:
            loc = page.locator(sel)
            if await loc.count() > 0:
                try:
                    await loc.first.click()
                    clicked = True
                    _log.info("  [EXPORT] 标准能力：已点击导出按钮 (%s)", sel[:50])
                    break
                except Exception as _e:
                    _log.debug("[export] 非关键异常: %s", _e, exc_info=True)
                    continue
        if not clicked:
            _log.info("  [EXPORT] 标准能力：未找到导出按钮")
            return False

        # ★ S4-011: 等待导出请求触发(文件生成可能需要时间)
        await page.wait_for_timeout(2000)
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception as _e:
            _log.debug("[export] 非关键异常: %s", _e, exc_info=True)

        # ── 2. 处理格式选择弹窗（若有）──
        format_selectors = [
            'button:has-text("Excel")',
            'button:has-text("CSV")',
            'button:has-text("PDF")',
            'button:has-text("确定")',
            'button:has-text("确认")',
            'button:has-text("OK")',
        ]
        for sel in format_selectors:
            loc = page.locator(sel)
            if await loc.count() > 0:
                try:
                    await loc.first.click()
                    _log.info("  [EXPORT] 标准能力：已选择格式/确认")
                    # ★ S4-011: 等待导出文件生成
                    await page.wait_for_timeout(1500)
                    break
                except Exception as _e:
                    _log.debug("[export] 非关键异常: %s", _e, exc_info=True)
                    continue

        # ── 3. 验证导出成功 ──
        body_text = await page.evaluate("() => document.body.innerText || ''") or ""
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
            _log.info("  [EXPORT] 标准能力：✓ 导出成功提示命中")
            return True

        # 检查是否有下载触发（通过 performance API）
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
                _log.info("  [EXPORT] 标准能力：✓ 检测到下载请求 %s", str(api_calls))
                return True
        except Exception as _e:
            _log.debug("[export] 非关键异常: %s", _e, exc_info=True)

        # 页面无错误提示也算成功（很多系统导出后静默下载）
        error_markers = ("导出失败", "下载失败", "错误", "失败", "error", "fail")
        if not any(m.lower() in body_text.lower() for m in error_markers):
            _log.info("  [EXPORT] 标准能力：✓ 无错误提示，假设导出成功")
            return True

        _log.info("  [EXPORT] 标准能力：未能确认导出成功，回退 AI")
        return False
    except Exception as e:
        _log.info("  [EXPORT] 标准能力异常，回退 AI: %s", str(e)[:120])
        return False
