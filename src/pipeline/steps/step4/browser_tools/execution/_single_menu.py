# -*- coding: utf-8 -*-
"""
菜单导航与点击模块（从 _single.py 拆分）
========================================
★ 架构债清理(2026-07-25): 菜单导航回退 + 通用菜单点击 + 关键词直搜 + 按钮disabled补救
  拆分至本文件（§4.3 单文件≤800行）。

职责边界：
  - _fallback_menu_navigate: URL导航失败时回退到菜单导航（含 direct_url 优先 + 验证层）
  - _click_menu_item_generic: 通用菜单项点击（ARIA + 主流UI框架 + 文本回退）
  - _click_menu_by_text: 按关键词列表逐个尝试点击
  - _try_remedy_disabled_button: 按钮 disabled 时自动填充容器内字段补救

通用性：零业务硬编码，纯基于通用 UI 框架选择器 + ARIA 语义 + 文本回退。
"""

import asyncio
from src.core.guarded import guard
import logging

_log = logging.getLogger(__name__)

# 依赖：导航路径查找模块（同目录）
from ._single_nav_lookup import (
    _extract_nav_keywords,
    _lookup_nav_path,
    _click_page_tab,
)

# 依赖：页面就绪条件等待（唯一实现，替代各处固定 sleep）
from src.core.page_ready import wait_page_ready

# 依赖：页面状态检测模块（同目录）
from ._single_nav_state import (
    _check_content_interactive,
    _check_error_page,
    _try_relogin_to_target,
    _find_first_working_page,
    _verify_nav_target_reached,
)


async def _fallback_menu_navigate(engine, url: str, hint: str) -> bool:
    """直接URL导航失败时，回退到菜单导航。

    从 system_nav_maps 查找目标页面的菜单路径，逐级点击菜单项。
    通用策略：不依赖具体UI框架，按文本匹配菜单项。
    """
    try:
        page = getattr(engine, "_page", None)
        if not page:
            return False

        # ★ 通用修复 (2026-07-17): 先导航到目标系统首页，确保菜单完整
        from src.core.config_loader import config

        target_url = config.target_url or ""
        if target_url:
            _log.info("[MenuNav] 先导航到目标系统首页: %s", target_url)
            with guard("_fallback_menu_navigate: await engine.goto(target_url)", log=_log, level=logging.WARNING):
                await engine.goto(target_url)
                await wait_page_ready(page)

            # ★ 通用修复 (2026-07-18): 页面内容健康检查
            #   检测死页面（如 /dashboard 无实际内容），自动导航到第一个有效页面
            _body_text = ""
            try:
                _body_text = await page.locator("body").text_content(timeout=3000) or ""
            except Exception as _se12:
                _log.debug(
                    "[_single] 异常降级(pass): %s", str(_se12)[:60], exc_info=True
                )
                pass
            if len(_body_text.strip()) < 50:
                _log.warning(
                    "[MenuNav] 首页内容不足(%d字符)，可能为死页面，尝试导航到首个有效页面",
                    len(_body_text.strip()),
                )
                _fallback_url = await _find_first_working_page()
                if _fallback_url:
                    with guard("_fallback_menu_navigate: await engine.goto(_fallback_url)", log=_log, level=logging.WARNING):
                        await engine.goto(_fallback_url)
                        await wait_page_ready(page)
                        _log.info("[MenuNav] 已导航到有效页面: %s", _fallback_url[:80])

        # 1. 从 URL 或 hint 提取目标页面关键词
        target_keywords = _extract_nav_keywords(url, hint)
        if not target_keywords:
            _log.info("[MenuNav] 无法从 URL/hint 提取关键词: url=%s", url[:80])
            return False

        _log.info("[MenuNav] 尝试菜单导航，关键词: %s", target_keywords)

        # 2. 查找 system_nav_maps 获取导航路径 + direct_url
        # ★ v4 (2026-07-20): _lookup_nav_path 返回 dict {nav_path, direct_url}
        #   通用性: 不同系统Tab实现方式不同
        #   - URL参数Tab(某SPA系统): direct_url 含 ?tab=xxx，直接URL导航
        #   - 路由Tab: direct_url 含 /path/tab-name，直接URL导航
        #   - 页面内Tab(ElementUI/Ant): direct_url 为空，走文本匹配 + _click_page_tab
        nav_result = await _lookup_nav_path(target_keywords)
        nav_path = nav_result.get("nav_path", []) if nav_result else []
        direct_url = nav_result.get("direct_url", "") if nav_result else ""

        if not nav_path and not direct_url:
            _log.info("[MenuNav] 未找到导航路径，尝试直接文本匹配")
            # 直接尝试在页面上找目标关键词点击
            # ★ 方案B验证层: 点击后验证是否真正到达目标页面(修复假成功)
            _clicked = await _click_menu_by_text(page, target_keywords)
            if _clicked and await _verify_nav_target_reached(page, target_keywords):
                return True
            _log.warning("[MenuNav] 文本匹配点击后未到达目标页面")
            return False

        # ★ v4 (2026-07-20): 优先使用 direct_url 进行URL导航
        #   通用场景: 某SPA系统Tab用URL参数实现（?tab=material_formula）
        #   直接URL导航比文本匹配更可靠，且能避免Tab点击选择器不匹配的问题
        #   通用性: 零业务硬编码，纯基于 nav_map 中的 path 字段
        if direct_url:
            _log.info("[MenuNav] 检测到 direct_url，优先URL导航: %s", direct_url[:100])
            # 拼接完整URL（如果 direct_url 是相对路径）
            from src.core.config_loader import config as _cfg2

            _base_url = getattr(_cfg2, "target_url", "") or ""
            if direct_url.startswith("http"):
                _full_url = direct_url
            elif _base_url and direct_url.startswith("/"):
                # 去掉 _base_url 末尾的 /
                _base = _base_url.rstrip("/")
                _full_url = _base + direct_url
            else:
                _full_url = direct_url

            with guard("_fallback_menu_navigate: await engine.goto(_full_url)", log=_log, level=logging.WARNING):
                await engine.goto(_full_url)
                await wait_page_ready(page)
                # 验证页面已加载
                _content_cnt = await _check_content_interactive(page)
                _is_error = await _check_error_page(page)
                _log.info(
                    "[MenuNav] direct_url 导航完成: content=%d, is_error=%s",
                    _content_cnt,
                    _is_error,
                )
                if not _is_error and _content_cnt >= 2:
                    # ★ 方案B验证层: 验证direct_url导航真正到达目标页面(修复假成功)
                    if await _verify_nav_target_reached(page, target_keywords):
                        _log.info("[MenuNav] direct_url 导航成功(目标验证通过)")
                        return True
                    _log.warning(
                        "[MenuNav] direct_url 导航后目标验证未通过,回退菜单文本匹配"
                    )
                else:
                    _log.warning(
                        "[MenuNav] direct_url 导航后页面无效(content=%d, error=%s)，回退到菜单文本匹配",
                        _content_cnt,
                        _is_error,
                    )
                    # 重新导航到首页，准备菜单文本匹配
                    if _base_url:
                        try:
                            await engine.goto(_base_url)
                            await wait_page_ready(page)
                        except Exception as _se13:
                            _log.debug(
                                "[_single] 异常降级(pass): %s",
                                str(_se13)[:60],
                                exc_info=True,
                            )
                            pass

        if not nav_path:
            _log.info("[MenuNav] direct_url 无效且无 nav_path，尝试直接文本匹配")
            # ★ 方案B验证层: 点击后验证是否真正到达目标页面(修复假成功)
            _clicked = await _click_menu_by_text(page, target_keywords)
            if _clicked and await _verify_nav_target_reached(page, target_keywords):
                return True
            _log.warning("[MenuNav] direct_url无效分支: 文本匹配后未到达目标页面")
            return False

        _log.info("[MenuNav] 导航路径: %s", " > ".join(nav_path))

        # 3. 逐级点击菜单
        last_clicked = 0
        unclicked_tabs = []  # ★ v3 (2026-07-20): 记录未点击的Tab名称
        for i, menu_name in enumerate(nav_path):
            clicked = await _click_menu_item_generic(page, menu_name)
            if not clicked:
                _log.warning("[MenuNav] 未找到菜单项 '%s' (第%d级)", menu_name, i + 1)
                # ★ v3 (2026-07-20): 如果已经点击了至少一级菜单，尝试将剩余项作为Tab点击
                if last_clicked > 0:
                    _log.info(
                        "[MenuNav] 已点击%d级菜单，剩余项尝试Tab点击: %s",
                        last_clicked,
                        nav_path[i:],
                    )
                    unclicked_tabs = nav_path[i:]
                    break
                return False
            last_clicked = i + 1
            await page.wait_for_timeout(500)
            _log.info("[MenuNav] 已点击菜单 '%s'", menu_name)

        # ★ v3 (2026-07-20): 点击页面内Tab（Ant Design .ant-tabs-tab / ElementUI .el-tabs__item）
        #   通用性: 覆盖主流UI框架的Tab选择器，零业务硬编码
        if unclicked_tabs:
            await page.wait_for_timeout(800)  # 等待页面加载
            for tab_name in unclicked_tabs:
                tab_clicked = await _click_page_tab(page, tab_name)
                if tab_clicked:
                    _log.info("[MenuNav] 已点击Tab '%s'", tab_name)
                    await page.wait_for_timeout(500)
                else:
                    _log.warning("[MenuNav] 未找到Tab '%s'，继续", tab_name)

        # 4. 验证页面已加载
        await page.wait_for_timeout(1000)
        is_error = await _check_error_page(page)
        if is_error:
            # ★ 通用修复 (2026-07-17): 菜单导航后仍为错误页面，尝试重新登录
            _log.warning("[MenuNav] 菜单导航后仍为错误页面，尝试重新登录")
            relogin_ok = await _try_relogin_to_target(engine, page)
            if relogin_ok:
                _log.info("[MenuNav] 重新登录成功，重试菜单导航")
                # ★ v4: 重试时也优先使用 direct_url
                if direct_url:
                    with guard("_fallback_menu_navigate: _base = (getattr(_cfg2, 'target_url'", log=_log, level=logging.WARNING):
                        _base = (getattr(_cfg2, "target_url", "") or "").rstrip("/")
                        _retry_url = (
                            direct_url
                            if direct_url.startswith("http")
                            else (
                                _base + direct_url
                                if direct_url.startswith("/")
                                else direct_url
                            )
                        )
                        await engine.goto(_retry_url)
                        await wait_page_ready(page)
                        _cnt = await _check_content_interactive(page)
                        _err = await _check_error_page(page)
                        if not _err and _cnt >= 2:
                            # ★ 方案B验证层: 重登后也验证目标到达(修复假成功)
                            if await _verify_nav_target_reached(page, target_keywords):
                                _log.info(
                                    "[MenuNav] 重登后 direct_url 导航成功(目标验证通过)"
                                )
                                return True
                            _log.warning("[MenuNav] 重登后 direct_url 目标验证未通过")
                # 回退到菜单文本匹配
                for i, menu_name in enumerate(nav_path):
                    clicked = await _click_menu_item_generic(page, menu_name)
                    if not clicked and i > 0:
                        # 重试Tab点击
                        await page.wait_for_timeout(800)
                        await _click_page_tab(page, menu_name)
                    await page.wait_for_timeout(500)
                await page.wait_for_timeout(1000)
                is_error = await _check_error_page(page)
                if not is_error:
                    # ★ 方案B验证层: 重登后菜单导航也验证目标到达(修复假成功)
                    if await _verify_nav_target_reached(page, target_keywords):
                        _log.info("[MenuNav] 重登后菜单导航成功(目标验证通过)")
                        return True
                    _log.warning("[MenuNav] 重登后菜单导航目标验证未通过")
            _log.warning("[MenuNav] 菜单导航失败")
            return False

        # ★ 方案B验证层: 主菜单导航成功点验证目标到达(修复假成功)
        if await _verify_nav_target_reached(page, target_keywords):
            _log.info("[MenuNav] 菜单导航成功(目标验证通过)")
            return True
        _log.warning("[MenuNav] 菜单导航后目标验证未通过,尝试重置+关键词直搜兜底")

        # ★ 方案B兜底 (ADR-017, 2026-07-23): 主导航路径验证失败时,重置菜单状态+关键词直搜
        #   根因(§10.9 3层追问):
        #     1. 表层: nav_path=[数据管理,物料管理,辅材管理] 点击后辅材管理未找到
        #     2. 中层: nav_map的parent_path(数据管理/物料管理)与实际DOM不一致,
        #            辅材管理实际不在物料管理下(可能在配方管理下或为独立菜单)
        #     3. 根因: Step1爬取的nav_map parent_path不可靠(SpaMenuExplorer 3/32成功),
        #            Step4不能盲信parent_path,需用关键词在完整菜单中直搜
        #   通用性(§12.4 9维度):
        #     1. 跨系统: 菜单文本搜索适用于所有SPA系统的侧边栏菜单
        #     2. 跨行业: 关键词匹配不依赖业务术语
        #     3. 跨页面: 重置到首页确保菜单完整可见
        #     4. 跨Tab: 直搜能命中Tab子页面菜单项
        #     5. 数据完整性: 不依赖nav_map的parent_path,纯DOM文本搜索
        #     6. 根因溯源: 修nav_map parent_path不可靠问题,非打补丁
        #     7. 覆盖性: 所有不命中parent_path的用例都受益
        #     8. 全局观: 兜底而非替代主流程,不影响正常导航
        #     9. 职责边界: Step4在自己职责内增强导航鲁棒性
        #   自我反驳三次:
        #     1) 不重置直接搜?→ 不行。点击物料管理后菜单展开态可能遮挡其他菜单项
        #     2) 遍历所有parent?→ 过重。重置+直搜更高效且通用
        #     3) 只搜第一级菜单?→ 不够。辅材可能在第二级,需搜所有可见菜单项
        with guard("_fallback_menu_navigate: from src.core.config_loader import c", log=_log, level=logging.WARNING):
            from src.core.config_loader import config as _cfg_reset

            _reset_url = getattr(_cfg_reset, "target_url", "") or ""
            if _reset_url:
                await engine.goto(_reset_url)
                await wait_page_ready(page)
                _log.info("[MenuNav] 已重置到首页,准备关键词直搜")
            # 关键词直搜所有可见菜单项(绕过不可靠的parent_path)
            _direct_clicked = await _click_menu_by_text(page, target_keywords)
            if _direct_clicked:
                await page.wait_for_timeout(1500)  # 等待页面加载
                if await _verify_nav_target_reached(page, target_keywords):
                    _log.info("[MenuNav] 关键词直搜导航成功(目标验证通过)")
                    return True
                _log.warning("[MenuNav] 关键词直搜点击成功但目标验证未通过")
            else:
                _log.warning("[MenuNav] 关键词直搜未找到匹配菜单项")

        _log.warning("[MenuNav] 菜单导航失败(所有路径均未通过验证)")
        return False

    except Exception as e:
        _log.warning("[MenuNav] 菜单导航异常: %s", str(e)[:120])
        return False


async def _click_menu_item_generic(page, menu_name: str) -> bool:
    """通用菜单项点击：按文本匹配，支持多种UI框架。

    不依赖特定CSS类名，使用通用选择器策略。
    """
    if not menu_name:
        return False

    # 策略1：ARIA role 语义选择器
    selectors = [
        f'[role="menuitem"]:has-text("{menu_name}")',
        f'[role="tab"]:has-text("{menu_name}")',
        f'[role="link"]:has-text("{menu_name}")',
        f'[role="button"]:has-text("{menu_name}")',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                first = loc.first
                if await first.is_visible():
                    await first.click(timeout=5000)
                    return True
        except Exception as _se18:
            _log.debug(
                "[_single] 异常降级(continue): %s", str(_se18)[:60], exc_info=True
            )
            continue

    # 策略2：通用元素选择器（Ant Design / ElementUI / 原生）
    selectors2 = [
        f'.ant-menu-item:has-text("{menu_name}")',
        f'.ant-menu-submenu-title:has-text("{menu_name}")',
        f'.el-menu-item:has-text("{menu_name}")',
        f'.el-submenu__title:has-text("{menu_name}")',
        f'nav a:has-text("{menu_name}")',
        f'.sidebar a:has-text("{menu_name}")',
        f'li:has-text("{menu_name}")',
        f'a:has-text("{menu_name}")',
        # DIV/SPAN-based sidebar items (common in SPA)
        f'div[class*="menu"]:has-text("{menu_name}")',
        f'div[class*="nav"]:has-text("{menu_name}")',
        f'div[class*="sidebar"]:has-text("{menu_name}")',
        f'div[class*="tab"]:has-text("{menu_name}")',
        f'span[class*="menu"]:has-text("{menu_name}")',
        f'span[class*="nav"]:has-text("{menu_name}")',
    ]
    for sel in selectors2:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                first = loc.first
                if await first.is_visible():
                    await first.click(timeout=5000)
                    return True
        except Exception as _se19:
            _log.debug(
                "[_single] 异常降级(continue): %s", str(_se19)[:60], exc_info=True
            )
            continue

    # 策略3：纯文本匹配（Playwright get_by_text）
    try:
        text_locs = page.get_by_text(menu_name, exact=False)
        cnt = await text_locs.count()
        for i in range(min(cnt, 10)):
            text_loc = text_locs.nth(i)
            if await text_loc.count() > 0 and await text_loc.is_visible():
                try:
                    await text_loc.click(timeout=3000)
                    return True
                except Exception:
                    # 尝试点击父元素
                    try:
                        parent = text_loc.locator("..")
                        if await parent.count() > 0 and await parent.is_visible():
                            await parent.click(timeout=3000)
                            return True
                    except Exception as _se20:
                        _log.debug(
                            "[_single] 异常降级(continue): %s",
                            str(_se20)[:60],
                            exc_info=True,
                        )
                        continue
    except Exception as _se21:
        _log.debug("[_single] 异常降级(pass): %s", str(_se21)[:60], exc_info=True)
        pass

    # 策略4：Tab 标签页匹配（常见于详情页内子导航）
    tab_selectors = [
        f'.ant-tabs-tab:has-text("{menu_name}")',
        f'.el-tabs__item:has-text("{menu_name}")',
        f'[role="tab"]:has-text("{menu_name}")',
        f'.tab-item:has-text("{menu_name}")',
    ]
    for sel in tab_selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                first = loc.first
                if await first.is_visible():
                    await first.click(timeout=5000)
                    return True
        except Exception as _se22:
            _log.debug(
                "[_single] 异常降级(continue): %s", str(_se22)[:60], exc_info=True
            )
            continue

    # ★ 通用修复 (2026-07-17): Tab 部分匹配回退
    # 如果完整 menu_name 不匹配，尝试去掉常见后缀（如"管理"、"列表"、"页面"）
    for suffix in ("管理", "列表", "页面", "模块", "中心", "配置", "设置"):
        if menu_name.endswith(suffix) and len(menu_name) > len(suffix) + 1:
            short_name = menu_name[: -len(suffix)]
            for sel in [
                f'.ant-tabs-tab:has-text("{short_name}")',
                f'[role="tab"]:has-text("{short_name}")',
            ]:
                try:
                    loc = page.locator(sel)
                    if await loc.count() > 0:
                        first = loc.first
                        if await first.is_visible():
                            await first.click(timeout=5000)
                            _log.info(
                                "[MenuNav] 通过部分匹配 '%s' -> '%s' 点击成功",
                                menu_name,
                                short_name,
                            )
                            return True
                except Exception as _se23:
                    _log.debug(
                        "[_single] 异常降级(continue): %s",
                        str(_se23)[:60],
                        exc_info=True,
                    )
                    continue
            break  # 只尝试第一个匹配的后缀

    return False


async def _click_menu_by_text(page, keywords: list) -> bool:
    """直接按关键词点击页面上的菜单项。"""
    for kw in keywords:
        if await _click_menu_item_generic(page, kw):
            _log.info("[MenuNav] 通过关键词 '%s' 点击成功", kw)
            return True
    return False


# ── 方案B：按钮 disabled 自动补救 ──
async def _try_remedy_disabled_button(page, button_locator, hint: str) -> bool:
    """当按钮 disabled 时，尝试查找同容器内的输入字段并填入占位数据。

    通用策略（零业务硬编码）：
    1. 查找按钮的父级容器：form > fieldset > [role=form] > 最近的 div 祖先
    2. 在容器内查找所有可交互的输入字段（排除 hidden/disabled/file/readonly）
    3. 对每个字段填入占位测试数据
    4. 重新检查按钮状态

    Returns:
        True: 补救成功，按钮变为 enabled
        False: 补救失败，按钮仍然 disabled
    """
    try:
        # 1. 查找按钮的父级容器
        _container = None
        for _sel in [
            "form",
            "fieldset",
            '[role="form"]',
            '[role="dialog"]',
            ".ant-modal-body",
        ]:
            try:
                _c = button_locator.locator("..")
                for _ in range(10):
                    _tag = await _c.evaluate("el => (el.tagName || '').toLowerCase()")
                    _role = await _c.evaluate(
                        "el => (el.getAttribute('role') || '').toLowerCase()"
                    )
                    _cls = await _c.evaluate("el => (el.className || '').toLowerCase()")
                    _sel_lower = (
                        _sel.replace('"', "")
                        .replace("[", "")
                        .replace("]", "")
                        .replace("=", "")
                        .replace(".", "")
                        .replace("'", "")
                    )
                    if _tag == _sel_lower or _role == _sel_lower or _sel_lower in _cls:
                        _container = _c
                        _log.info(
                            "[Remedy] 找到按钮容器: %s (tag=%s role=%s)",
                            _sel,
                            _tag,
                            _role,
                        )
                        break
                    _c = _c.locator("..")
                if _container:
                    break
            except Exception as _se24:
                _log.debug(
                    "[_single] 异常降级(continue): %s", str(_se24)[:60], exc_info=True
                )
                continue
        if not _container:
            _log.info("[Remedy] 未找到按钮容器，放弃自动补救: %s", hint)
            return False

        # 2. 查找容器内的可填充字段
        _filled_count = 0
        for _input_sel in [
            'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([disabled]):not([readonly])',
            "textarea:not([disabled]):not([readonly])",
            "select:not([disabled])",
        ]:
            try:
                _inputs = _container.locator(_input_sel)
                _count = await _inputs.count()
                for _i in range(_count):
                    _inp = _inputs.nth(_i)
                    if not await _inp.is_visible():
                        continue
                    _tag = await _inp.evaluate("el => (el.tagName || '').toLowerCase()")
                    _type = await _inp.evaluate("el => (el.type || '').toLowerCase()")

                    if _tag == "select":
                        # 下拉框：选择第一个非空选项
                        _options = _inp.locator("option")
                        _opt_count = await _options.count()
                        for _oi in range(_opt_count):
                            _opt_val = await _options.nth(_oi).get_attribute("value")
                            if _opt_val and _opt_val.strip():
                                await _inp.select_option(value=_opt_val)
                                _filled_count += 1
                                _log.info("[Remedy] 填充下拉框: value=%s", _opt_val)
                                break
                    elif _type in ("date", "datetime-local", "month", "week"):
                        await _inp.fill("2025-01-01")
                        _filled_count += 1
                        _log.info("[Remedy] 填充日期字段: 2025-01-01")
                    elif _type == "number":
                        await _inp.fill("1")
                        _filled_count += 1
                        _log.info("[Remedy] 填充数字字段: 1")
                    else:
                        # 文本输入框：优先用 placeholder，否则用占位值
                        _ph = await _inp.get_attribute("placeholder")
                        _fill_val = _ph if _ph else "TEST"
                        await _inp.fill(_fill_val)
                        _filled_count += 1
                        _log.info("[Remedy] 填充文本字段: %s", _fill_val[:30])
            except Exception as _e:
                _log.debug("[Remedy] 填充字段异常: %s", str(_e)[:80])
                continue

        if _filled_count == 0:
            _log.info("[Remedy] 未找到可填充字段，放弃自动补救: %s", hint)
            return False

        # 3. 等待页面响应（某些表单在输入后有异步校验）
        await asyncio.sleep(0.3)

        # 4. 重新检查按钮状态
        _still_disabled = await button_locator.is_disabled()
        if not _still_disabled:
            _log.info(
                "[Remedy] ✓ 自动补救成功: %s（填充了 %d 个字段）", hint, _filled_count
            )
            return True
        _log.info(
            "[Remedy] ✗ 自动补救失败: %s 仍然 disabled（填充了 %d 个字段）",
            hint,
            _filled_count,
        )
        return False
    except Exception as _e:
        _log.debug("[Remedy] 自动补救异常: %s", str(_e)[:120])
        return False
