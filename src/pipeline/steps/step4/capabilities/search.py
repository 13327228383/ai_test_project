# -*- coding: utf-8 -*-
"""
标准搜索/筛选能力
"""

import asyncio
import logging

_log = logging.getLogger(__name__)


async def try_search_or_filter(engine, goal: str) -> bool:
    """
    标准搜索/筛选能力：从 goal/step_desc 中提取搜索关键词，
    用语义选择器定位搜索框和按钮，填入关键词并点击搜索，再验证结果出现。

    仅用于 capability_hint = search_or_filter 类业务意图。
    返回 True 表示已成功完成搜索/筛选；False 表示回退 AI 路径。
    """
    page = engine._page if engine else None
    if page is None:
        return False
    try:
        # ── 1. 提取搜索关键词 ──
        import re as _re

        search_kw = ""
        kw_patterns = [
            r'搜(?:索|寻)(?:["\'\s]*)([\u4e00-\u9fff\w]+)',
            r'查(?:询|找|看)(?:["\'\s]*)([\u4e00-\u9fff\w]+)',
            r'筛(?:选)(?:["\'\s]*)([\u4e00-\u9fff\w]+)',
            r'(?:关键词|关键字|输入)[：:]\s*["\']?([\u4e00-\u9fff\w]+)',
            r'填入["\'\s]*([\u4e00-\u9fff\w]+)["\'\s]*',
            r'输入["\'\s]*([\u4e00-\u9fff\w]+)["\'\s]*',
        ]
        for pat in kw_patterns:
            m = _re.search(pat, goal)
            if m and m.group(1):
                candidate = m.group(1).strip()
                # 过滤掉常见虚词，至少 2 个字符
                noise = {
                    "用户名",
                    "密码",
                    "账号",
                    "按钮",
                    "搜索框",
                    "输入框",
                    "的",
                    "和",
                    "了",
                    "在",
                    "是",
                }
                if len(candidate) >= 2 and candidate not in noise:
                    search_kw = candidate
                    break
        if not search_kw:
            # 从 goal 中取第一个有业务含义的中文词组作 fallback（从前往后找，优先业务名词）
            chinese_words = _re.findall(r"[\u4e00-\u9fff]{2,6}", goal)
            fallback_noise = {
                "完成",
                "实现",
                "验证",
                "测试",
                "测试用例",
                "搜索",
                "查询",
                "筛选",
                "功能",
                "页面",
                "列表",
                "结果",
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
                "功能验证",
                "测试验证",
                "有效性",
                "正确性",
                "完整性",
                "验证功能",
                "测试功能",
                "验证测试",
                "验证功能测试",
                "功能测试验证",
            }
            candidate = ""
            for w in chinese_words:
                if w not in fallback_noise:
                    candidate = w
                    break
            if candidate:
                search_kw = candidate
        if not search_kw:
            _log.info("  [SEARCH] 标准能力：未能从 goal 提取搜索关键词")
            return False
        _log.info(
            "  [SEARCH] 标准能力：提取关键词='%s' goal='%s'", search_kw, goal[:80]
        )

        # ── 2. 定位搜索输入框 ──
        search_input_selectors = [
            'input[placeholder*="搜索"]',
            'input[placeholder*="查询"]',
            'input[placeholder*="查找"]',
            'input[placeholder*="筛选"]',
            'input[placeholder*="关键词"]',
            'input[placeholder*="输入"]',
            'input[name="search"]',
            'input[name="keyword"]',
            'input[name="keywords"]',
            'input[name="query"]',
            'input[name="filter"]',
            'input[type="search"]',
            'input[aria-label*="搜索"]',
            'input[aria-label*="查询"]',
        ]
        input_loc = None
        for sel in search_input_selectors:
            loc = page.locator(sel)
            if await loc.count() > 0:
                input_loc = loc.first
                break
        if input_loc is None:
            _log.info("  [SEARCH] 标准能力：未找到搜索输入框，回退 AI")
            return False

        # ── 3. 填入关键词 ──
        await input_loc.click()
        await input_loc.fill("")
        await input_loc.fill(search_kw)
        _log.info("  [SEARCH] 标准能力：已填入 '%s'", search_kw)

        # ── 4. 点击搜索按钮 ──
        search_btn_selectors = [
            'button:has-text("搜索")',
            'button:has-text("查询")',
            'button:has-text("查找")',
            'button:has-text("搜 索")',
            'button:has-text("查 询")',
            'button:has-text("Search")',
            'button[type="submit"]',
            'button i[class*="search"]',
            'button[aria-label*="搜索"]',
            'button[aria-label*="查询"]',
            ".search-btn",
            ".search-button",
            'input[type="submit"]',
        ]
        btn_clicked = False
        for sel in search_btn_selectors:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click()
                btn_clicked = True
                _log.info("  [SEARCH] 标准能力：已点击搜索按钮 (%s)", sel[:50])
                break
        if not btn_clicked:
            await page.keyboard.press("Enter")
            _log.info("  [SEARCH] 标准能力：按 Enter 键触发搜索")

        # ── 5. 等待结果加载 ──
        # ★ S4-011: 删除冗余 sleep,直接用 wait_for_load_state 确定性等待
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception as _e:
            _log.debug("[search] 非关键异常: %s", _e, exc_info=True)

        # ── 6. 验证搜索结果 ──
        # 6a. 检查是否有表格/列表行变化（最直观的证据）
        try:
            row_count = await page.locator(
                'table tbody tr, [role="row"], [class*="table"] [class*="row"], '
                '[class*="list"] [class*="item"], ul li'
            ).count()
            if row_count > 0:
                _log.info("  [SEARCH] 标准能力：✓ 搜索结果 %d 行", row_count)
                return True
        except Exception as _e:
            _log.debug("[search] 非关键异常: %s", _e, exc_info=True)

        # 6b. 检查页面文本是否包含搜索关键词（说明结果中出现了）
        try:
            body_text = await page.evaluate("() => document.body.innerText || ''") or ""
            if search_kw in body_text:
                _log.info("  [SEARCH] 标准能力：✓ 页面文本包含关键词 '%s'", search_kw)
                return True
        except Exception as _e:
            _log.debug("[search] 非关键异常: %s", _e, exc_info=True)

        # 6c. 检查是否有空状态提示
        try:
            empty_selectors = [
                "text=暂无数据",
                "text=没有数据",
                "text=未找到",
                "text=无匹配",
                "text=No data",
                "text=No results",
                ".empty",
                '[class*="empty"]',
            ]
            for es in empty_selectors:
                if await page.locator(es).count() > 0:
                    _log.info(
                        "  [SEARCH] 标准能力：✓ 显示空结果提示（仍算搜索功能正常）"
                    )
                    return True
        except Exception as _e:
            _log.debug("[search] 非关键异常: %s", _e, exc_info=True)

        # 6d. 检查 API 轨迹：搜索通常触发 GET 请求
        try:
            api_calls_raw = await page.evaluate(
                """
                () => {
                    const entries = performance.getEntriesByType('resource') || [];
                    return entries.filter(e => e.initiatorType === 'xmlhttprequest' || e.initiatorType === 'fetch')
                                  .slice(-5).map(e => ({url: e.name, status: 0}));
                }
            """
            )
            if api_calls_raw and len(api_calls_raw) > 0:
                _log.info(
                    "  [SEARCH] 标准能力：✓ 检测到 %d 个 API 请求（搜索触发后端调用）",
                    len(api_calls_raw),
                )
                return True
        except Exception as _e:
            _log.debug("[SEARCH] API检测失败，回退AI: %s", _e, exc_info=True)

        _log.info("  [SEARCH] 标准能力：未能确认搜索结果，回退 AI")
        return False
    except Exception as e:
        _log.info("  [SEARCH] 标准能力异常，回退 AI: %s", str(e)[:120])
        return False
