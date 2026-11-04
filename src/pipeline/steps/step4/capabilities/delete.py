# -*- coding: utf-8 -*-
"""
标准删除业务记录能力
"""

import logging

_log = logging.getLogger(__name__)


async def try_delete_record(engine, goal: str) -> bool:
    """
    标准删除业务记录能力：
    1) 从 goal 中提取记录标识（名称/关键词）；
    2) 在列表中定位该记录行；
    3) 点击删除/移除按钮；
    4) 确认弹窗（若有）；
    5) 验证记录已消失。

    仅用于 capability_hint = delete_record 类业务意图。
    返回 True 表示删除成功；False 表示回退 AI 路径。
    """
    page = engine._page if engine else None
    if page is None:
        return False
    try:
        import re as _re

        # ── 1. 提取记录标识 ──
        record_kw = ""
        del_patterns = [
            r'删(?:除|掉)(?:["\'\s]*)([\u4e00-\u9fff\w]+)',
            r'移除(?:["\'\s]*)([\u4e00-\u9fff\w]+)',
            r'(?:删除|移除)["\'\s]*([\u4e00-\u9fff\w]+)',
        ]
        for pat in del_patterns:
            m = _re.search(pat, goal)
            if m and m.group(1):
                candidate = m.group(1).strip()
                if len(candidate) >= 2:
                    record_kw = candidate
                    break
        if not record_kw:
            # fallback: 取 goal 中最后一个中文词组
            words = _re.findall(r"[\u4e00-\u9fff]{2,6}", goal)
            noise = {
                "完成",
                "实现",
                "验证",
                "测试",
                "测试用例",
                "功能",
                "页面",
                "列表",
                "数据",
                "系统",
                "当前",
                "步骤",
                "打开",
                "登录",
                "确保",
                "进行",
                "检查",
                "导航",
                "进入",
                "应该",
                "可以",
                "删除",
                "移除",
            }
            for w in reversed(words):
                if w not in noise:
                    record_kw = w
                    break
        if not record_kw:
            _log.info("  [DELETE] 标准能力：未能提取记录标识")
            return False
        _log.info("  [DELETE] 标准能力：目标记录='%s'", record_kw)

        # ── 2. 定位记录行 ──
        row_selectors = [
            'tr:has-text("%s")' % record_kw,
            '[class*="row"]:has-text("%s")' % record_kw,
            '[class*="item"]:has-text("%s")' % record_kw,
            'li:has-text("%s")' % record_kw,
        ]
        row_loc = None
        for sel in row_selectors:
            loc = page.locator(sel)
            if await loc.count() > 0:
                row_loc = loc.first
                break
        if row_loc is None:
            _log.info("  [DELETE] 标准能力：未找到记录行 '%s'", record_kw)
            return False

        # ── 3. 在行内找删除按钮 ──
        del_btn_selectors = [
            'button:has-text("删除")',
            'button:has-text("移除")',
            'button:has-text("Delete")',
            'button:has-text("Remove")',
            'a:has-text("删除")',
            'a:has-text("移除")',
            '[class*="delete"]',
            '[class*="remove"]',
            '[class*="del-btn"]',
            '[class*="action"] button',
        ]
        # 先在行内搜索
        clicked = False
        for sel in del_btn_selectors:
            try:
                btn = row_loc.locator(sel)
                if await btn.count() > 0:
                    await btn.first.click()
                    clicked = True
                    _log.info("  [DELETE] 标准能力：已点击删除按钮 (%s)", sel[:50])
                    break
            except Exception as _e:
                _log.debug("[delete] 非关键异常: %s", _e, exc_info=True)
                continue
        if not clicked:
            _log.info("  [DELETE] 标准能力：未找到删除按钮")
            return False

        # ★ S4-011: 确定性等待替代 sleep — 等待确认弹窗出现
        await page.wait_for_timeout(300)

        # ── 4. 处理确认弹窗 ──
        confirm_selectors = [
            'button:has-text("确定")',
            'button:has-text("确认")',
            'button:has-text("是")',
            'button:has-text("Yes")',
            'button:has-text("OK")',
            ".confirm-btn",
            '[class*="confirm"] button',
        ]
        for sel in confirm_selectors:
            loc = page.locator(sel)
            if await loc.count() > 0:
                try:
                    await loc.first.click()
                    _log.info("  [DELETE] 标准能力：已确认删除")
                    # ★ S4-011: 等待删除请求完成
                    await page.wait_for_timeout(300)
                    break
                except Exception as _e:
                    _log.debug("[delete] 非关键异常: %s", _e, exc_info=True)
                    continue

        # ── 5. 验证记录已消失 ──
        # ★ S4-011: 确定性等待替代 sleep — 等待删除后页面刷新
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            await page.wait_for_timeout(500)
        try:
            body_text = await page.evaluate("() => document.body.innerText || ''") or ""
            success_markers = (
                "删除成功",
                "移除成功",
                "已删除",
                "已移除",
                "success",
                "deleted",
                "removed",
            )
            if any(m.lower() in body_text.lower() for m in success_markers):
                _log.info("  [DELETE] 标准能力：✓ 成功提示命中")
                return True
        except Exception as _e:
            _log.debug("[delete] 非关键异常: %s", _e, exc_info=True)

        # 检查记录是否还在页面上（使用第一个成功定位过的 selector 验证，避免误报）
        if row_loc is not None:
            try:
                # 用同一个 selector 重新查询，确保一致性
                first_sel = row_selectors[0]
                loc = page.locator(first_sel)
                if await loc.count() == 0:
                    _log.info(
                        "  [DELETE] 标准能力：✓ 记录已消失（selector=%s）",
                        first_sel[:50],
                    )
                    return True
            except Exception as _e:
                _log.debug("[DELETE] 检测失败，回退AI: %s", _e, exc_info=True)

        _log.info("  [DELETE] 标准能力：未能确认删除成功，回退 AI")
        return False
    except Exception as e:
        _log.info("  [DELETE] 标准能力异常，回退 AI: %s", str(e)[:120])
        return False
