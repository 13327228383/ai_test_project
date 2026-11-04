# -*- coding: utf-8 -*-
"""P2-10 ElementMixin — 元素发现、快照、匹配（~350行）。

通用性：只用W3C ARIA属性+原生HTML标签，零框架特定类名。
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any

from src.agent.element_utils import (
    get_page_snapshot,
    extract_keywords,
    match_keywords_to_elements,
)
from src.core.page_ready import (
    await_bounded,
    count_bounded,
    evaluate_bounded,
)

_log = logging.getLogger(__name__)


class ElementMixin:
    """元素发现与匹配能力。

    提供：页面快照、折叠菜单展开、智能元素匹配、关键词提取。
    跨框架/跨行业通用。
    """

    # ── 页面快照获取（薄封装）──

    async def _get_page_snapshot(self, page) -> tuple[list[dict], list[dict]]:
        """获取页面实时交互元素（委托给 element_utils.get_page_snapshot）。"""
        return await get_page_snapshot(page)

    # ── 关键词提取（薄封装）──

    def _extract_keywords(self, operation: str) -> list[str]:
        """提取中文关键词（委托给 element_utils.extract_keywords）。"""
        return extract_keywords(operation)

    # ── 智能元素匹配 ──

    async def _match_elements_smart(
        self,
        keywords: list[str],
        elements: list[dict],
        operation: str,
    ) -> dict[str, Any] | None:
        """智能元素匹配 — 关键词优先 + LLM语义兜底。

        策略: 1) 关键词匹配 2) score<5 → LLM语义匹配 3) 返回最佳结果。
        通用性: 关键词处理简单场景，LLM处理复杂语义（如"辅材配方"vs"辅材"）。
        """
        _kw_match = match_keywords_to_elements(keywords, elements)
        if _kw_match and _kw_match["score"] >= 5:
            return _kw_match
        # 关键词匹配失败或分数低 → LLM语义兜底
        _sem_match = await self._semantic_element_match(operation, elements)
        if _sem_match:
            return _sem_match
        return _kw_match  # LLM也失败，返回关键词结果（可能为None或低分）

    # ── 折叠菜单展开 ──

    async def _expand_and_capture_full_menu(self, page, timeout_ms: int) -> list[str]:
        """★ 方案B v9(2026-07-31): 三阶段展开——hover+click+JS提取，通用覆盖所有菜单系统。

        根因(§10.9 5层why):
          L1: 导航失败 → 子菜单项"烟叶画像"未被提取
          L2: 子菜单项缺失 → hover未展开Ant Design子菜单（需click展开）
          L3: 只覆盖hover → 不同框架展开方式不同（hover/click/aria-expanded）
          L4: 旧方案只有hover+fallback → 但hover成功返回5项就不进fallback
          L5: 根本原因——菜单展开策略不通用，需同时覆盖hover和click两种展开方式

        通用性: 跨所有框架 — hover覆盖mouseenter展开(hover触发)，
               click覆盖aria-expanded展开(click触发)，零框架特定选择器。
        策略: ① JS提取初始可见项 → ② hover所有li(触发mouseenter型展开)
              → ③ click所有[aria-expanded=false](触发click型展开)
              → ④ 等待渲染 → ⑤ JS提取全部项（含懒渲染的子菜单项）
        """
        import asyncio

        # ── 阶段1: JS提取初始菜单项 ──
        _js_items = await self._extract_menu_items_via_js(page)
        _log.info(
            "[FullMenu] 阶段1 JS提取: %d项 %s",
            len(_js_items) if _js_items else 0,
            (_js_items[:8] if _js_items else []),
        )

        # ── 阶段2: Playwright hover展开子菜单（覆盖mouseenter型展开）──
        _hovered = 0
        with guard("_expand_and_capture_full_menu: _sidebar_sel = ( 'nav, aside, [role=", log=_log, level=logging.WARNING):
            _sidebar_sel = (
                "nav, aside, [role='navigation'], [class*='sider'], [class*='sidebar']"
            )
            _sidebar = page.locator(_sidebar_sel).first
            if await count_bounded(_sidebar) > 0:
                _all_lis = _sidebar.locator("li")
                _li_count = await count_bounded(_all_lis)
                _log.info("[FullMenu] 阶段2 hover: 侧边栏中%d个li元素", _li_count)
                for _i in range(min(_li_count, 30)):
                    with guard("_expand_and_capture_full_menu: await _all_lis.nth(_i).hover(timeout", log=_log):
                        await _all_lis.nth(_i).hover(timeout=2000)
                        _hovered += 1
                        await asyncio.sleep(0.08)
                _log.info("[FullMenu] 阶段2 hover了%d个li元素", _hovered)
            else:
                _all_lis = page.locator("li")
                _li_count = await count_bounded(_all_lis)
                for _i in range(min(_li_count, 30)):
                    with guard("_expand_and_capture_full_menu: await _all_lis.nth(_i).hover(timeout", log=_log):
                        await _all_lis.nth(_i).hover(timeout=2000)
                        _hovered += 1

        # ── 阶段2b: click展开[aria-expanded=false]（覆盖click型展开,如Ant Design）──
        #   根因(2026-07-31): 某SPA系统 Ant Design子菜单需click展开，hover不触发。
        #   通用性: aria-expanded是W3C标准属性，跨框架通用。
        #   修复(2026-07-31 v2): ①限定侧边栏范围 ②force=True绕过遮挡 ③JS click兜底。
        _clicked = 0
        with guard("_expand_and_capture_full_menu: _sidebar_sel = ( 'nav, aside, [role=", log=_log, level=logging.WARNING):
            # ★ 限定侧边栏范围（避免点击页面其他区域的aria-expanded元素）
            _sidebar_sel = (
                "nav, aside, [role='navigation'], [class*='sider'], [class*='sidebar']"
            )
            _sidebar = page.locator(_sidebar_sel).first
            if await count_bounded(_sidebar) > 0:
                _collapsed = _sidebar.locator("[aria-expanded='false']")
            else:
                _collapsed = page.locator("[aria-expanded='false']")
            _cc = await count_bounded(_collapsed)
            _log.info(
                "[FullMenu] 阶段2b click: %d个[aria-expanded=false]元素(侧边栏内)", _cc
            )
            for _i in range(min(_cc, 20)):
                try:
                    # ★ force=True: 绕过Playwright actionability检查（遮挡/不可见/动画中）
                    await _collapsed.nth(_i).click(force=True, timeout=3000)
                    _clicked += 1
                    await asyncio.sleep(0.15)
                except Exception:
                    # ★ JS click兜底: Playwright click失败时，用JS dispatchEvent
                    with guard("_expand_and_capture_full_menu: await _collapsed.nth(_i).evaluate( '", log=_log):
                        await _collapsed.nth(_i).evaluate(
                            "el => el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}))"
                        )
                        _clicked += 1
                        await asyncio.sleep(0.15)
            _log.info("[FullMenu] 阶段2b click了%d个折叠元素", _clicked)

        # ── 阶段2c: Playwright提取弹出层子菜单项（★ v10 2026-07-31）──
        #   根因(§10.9): Ant Design vertical模式子菜单通过Portal渲染(.ant-menu-submenu-popup)
        #   到document.body，不在侧边栏容器内。弹出层hover关闭后消失，需在关闭前提取。
        #   通用性: 覆盖所有框架的弹出层——Ant Design/Element UI/通用popup。
        _popup_items = []
        _popup_sels = [
            ".ant-menu-submenu-popup li",
            ".el-menu--popup li",
            "[class*='submenu-popup'] li",
            "[class*='menu-popup'] li",
            "[role='menu'] li",
            ".ant-dropdown li",
            ".el-dropdown-menu li",
        ]
        for _psel in _popup_sels:
            with guard("_expand_and_capture_full_menu: _pop_locs = page.locator(_psel)", log=_log):
                _pop_locs = page.locator(_psel)
                _pc = await count_bounded(_pop_locs)
                if _pc > 0:
                    for _pi in range(min(_pc, 30)):
                        with guard("_expand_and_capture_full_menu: _pt = ( await _pop_locs.nth(_pi).tex", log=_log):
                            _pt = (
                                await await_bounded(
                                    _pop_locs.nth(_pi).text_content(),
                                    default="",
                                    what="text_content",
                                )
                                or ""
                            ).strip()
                            if (
                                _pt
                                and len(_pt) >= 2
                                and len(_pt) <= 50
                                and _pt not in _popup_items
                            ):
                                _popup_items.append(_pt)
        if _popup_items:
            _log.info(
                "[FullMenu] 阶段2c Playwright弹出层提取: %d项 %s",
                len(_popup_items),
                _popup_items[:10],
            )

        # ── 阶段2d: 补充提取——使用Playwright获取侧边栏内所有可见li文本（★ v10.1）──
        #   根因: JS提取的_processLi可能因DOM结构差异遗漏某些菜单项。
        #   策略: Playwright直接获取侧边栏内所有li的textContent，去重合并。
        _pw_items = []
        with guard("_expand_and_capture_full_menu: _sidebar_sel = ( 'nav, aside, [role=", log=_log, level=logging.WARNING):
            _sidebar_sel = (
                "nav, aside, [role='navigation'], [class*='sider'], [class*='sidebar']"
            )
            _sidebar = page.locator(_sidebar_sel).first
            if await count_bounded(_sidebar) > 0:
                _pw_lis = _sidebar.locator("li")
                _pw_lc = await count_bounded(_pw_lis)
                _log.info("[FullMenu] 阶段2d Playwright: 侧边栏内%d个li元素", _pw_lc)
                for _pwi in range(min(_pw_lc, 60)):
                    with guard("_expand_and_capture_full_menu: _pwt = (await _pw_lis.nth(_pwi).text", log=_log):
                        _pwt = (
                            await await_bounded(
                                _pw_lis.nth(_pwi).text_content(),
                                default="",
                                what="text_content",
                            )
                            or ""
                        ).strip()
                        _pwt = " ".join(_pwt.split())  # 规范化空白
                        if (
                            _pwt
                            and len(_pwt) >= 2
                            and len(_pwt) <= 50
                            and _pwt not in _pw_items
                        ):
                            _pw_items.append(_pwt)
        if _pw_items:
            _log.info(
                "[FullMenu] 阶段2d Playwright提取: %d项 %s",
                len(_pw_items),
                _pw_items[:15],
            )

        # ── 阶段2e: [role='menuitem'] Playwright全局提取（★ v11.1 2026-07-31）──
        #   根因(§10.9): _get_sidebar_items 使用 [role='menuitem'] 能捕获11项(含子菜单"烟叶画像")，
        #   但 _expand_and_capture_full_menu 主流程没有此提取阶段，导致子菜单项遗漏。
        #   通用性: [role='menuitem'] 是W3C ARIA标准属性，跨React/Vue/Angular通用。
        #   策略: 全局提取(不限侧边栏)，因为Portal渲染的子菜单项可能不在侧边栏容器内。
        _menuitem_items = []
        with guard("_expand_and_capture_full_menu: for _msel in ['[role='menuitem']', '", log=_log, level=logging.WARNING):
            for _msel in ["[role='menuitem']", "[role='treeitem']"]:
                _m_locs = page.locator(_msel)
                _mc = await count_bounded(_m_locs)
                if _mc > 0:
                    for _mi in range(min(_mc, 50)):
                        with guard("_expand_and_capture_full_menu: _mt = (await _m_locs.nth(_mi).text_c", log=_log):
                            _mt = (
                                await await_bounded(
                                    _m_locs.nth(_mi).text_content(),
                                    default="",
                                    what="text_content",
                                )
                                or ""
                            ).strip()
                            _mt = " ".join(_mt.split())
                            if (
                                _mt
                                and len(_mt) >= 2
                                and len(_mt) <= 50
                                and _mt not in _menuitem_items
                            ):
                                _menuitem_items.append(_mt)
        if _menuitem_items:
            _log.info(
                "[FullMenu] 阶段2e menuitem提取: %d项 %s",
                len(_menuitem_items),
                _menuitem_items[:15],
            )

        await asyncio.sleep(0.5)  # 等待React渲染所有子菜单

        # ── 阶段3: JS再次提取（含懒渲染的子菜单项）──
        _js_items2 = await self._extract_menu_items_via_js(page)
        if _js_items2:
            _log.info(
                "[FullMenu] 阶段3 JS提取: %d项 %s", len(_js_items2), _js_items2[:10]
            )
            # 合并去重: 阶段1 + 阶段2c弹出层 + 阶段2d侧边栏li + 阶段2e menuitem + 阶段3
            _seen = set(_js_items)
            _all = list(_js_items)
            # ★ v11.1: 合并所有补充提取阶段（2c弹出层 + 2d侧边栏li + 2e menuitem）
            for _item in _popup_items + _pw_items + _menuitem_items:
                if _item not in _seen:
                    _seen.add(_item)
                    _all.append(_item)
            for _item in _js_items2:
                if _item not in _seen:
                    _seen.add(_item)
                    _all.append(_item)
            _log.info(
                "[FullMenu] 合并后总计: %d项 (hover=%d click=%d popup=%d pw_li=%d menuitem=%d)",
                len(_all),
                _hovered,
                _clicked,
                len(_popup_items),
                len(_pw_items),
                len(_menuitem_items),
            )
            return _all

        # ── Fallback: aria-expanded点击展开（兜底）──
        # ★ v11.1: 合并所有补充提取阶段（2c弹出层 + 2d侧边栏li + 2e menuitem）
        if _popup_items or _pw_items or _menuitem_items:
            _seen = set(_js_items)
            _all = list(_js_items)
            for _item in _popup_items + _pw_items + _menuitem_items:
                if _item not in _seen:
                    _seen.add(_item)
                    _all.append(_item)
            _log.info(
                "[FullMenu] 补充提取: %d项 (阶段1=%d + popup=%d pw_li=%d menuitem=%d)",
                len(_all),
                len(_js_items),
                len(_popup_items),
                len(_pw_items),
                len(_menuitem_items),
            )
            if len(_all) > len(_js_items):
                return _all

        _log.info("[FullMenu] JS混合方案失败，使用aria-expanded fallback")
        return await self._fallback_expand_and_capture(page, timeout_ms)

    async def _extract_menu_items_via_js(self, page) -> list:
        """★ 方案B v8: 使用JS从DOM中提取侧边栏菜单项（含隐藏），完全通用。

        通用性: 零框架依赖——不依赖任何CSS类名或ARIA属性。
        策略: ① 找到侧边栏容器（多策略fallback）→ ② 遍历li元素提取直接文本
              → ③ 过滤去重 → ④ 容器找不到时fallback到全页li
        速度: <1s（一次JS调用），vs 旧方案15s（多轮点击+等待）
        """
        _js_code = """
        () => {
            const items = [];
            const seen = new Set();

            // ① 找到侧边栏容器（通用策略：多选择器fallback）
            const containers = [];
            const selList = [
                'nav', 'aside',
                '[role="navigation"]',
                '[class*="sider"]', '[class*="sidebar"]',
                '[class*="side-menu"]', '[class*="side_menu"]',
                '[class*="menu-container"]',
                'ul[class*="menu"]',
                // Ant Design / Element UI 特定容器（放最后，优先用通用选择器）
                '.ant-layout-sider', '.el-menu',
            ];
            for (const sel of selList) {
                try {
                    const els = document.querySelectorAll(sel);
                    els.forEach(el => {
                        if (!containers.includes(el)) containers.push(el);
                    });
                } catch(e) {}
            }

            // ★ v8.1: 过滤容器——只保留包含li元素的容器，且优先选li最多的
            containers.sort((a, b) => {
                const aLis = (a.querySelectorAll('li') || []).length;
                const bLis = (b.querySelectorAll('li') || []).length;
                return bLis - aLis;
            });

            const _processLi = (li) => {
                // 获取直接文本：遍历childNodes，跳过嵌套ul/ol中的li
                let text = '';
                for (const child of li.childNodes) {
                    if (child.nodeType === Node.TEXT_NODE) {
                        text += child.textContent;
                    } else if (child.nodeType === Node.ELEMENT_NODE) {
                        const tag = child.tagName.toLowerCase();
                        // 跳过嵌套的ul/ol（子菜单容器）
                        if (tag === 'ul' || tag === 'ol') continue;
                        // 如果子元素本身不包含li（是叶子节点），取文本
                        if (!child.querySelector('li')) {
                            text += ' ' + (child.textContent || '').trim();
                        }
                    }
                }
                text = text.trim().replace(/\\s+/g, ' ');
                // 过滤：长度2-50，非纯数字/符号，去重
                if (text && text.length >= 2 && text.length <= 50
                    && !seen.has(text)
                    && !/^[\\d\\s.\\-+*/×÷=<>()[\\]{}|&^%$#@!~`':;",?/\\\\]+$/.test(text)
                ) {
                    seen.add(text);
                    items.push(text);
                }
            };

            // ② 从容器中提取li元素
            if (containers.length > 0) {
                for (const container of containers) {
                    const lis = container.querySelectorAll('li');
                    for (const li of lis) {
                        _processLi(li);
                    }
                    // 如果第一个容器（li最多）已经有足够多的项，跳过其他容器
                    if (items.length >= 30) break;
                }
            }

            // ★ 通用性修复: 无 li 侧边栏兜底 — 现代 UI（Bootstrap/自定义 CSS）
            //   常以 <a class="nav-item"> 或 <button> 直接布局侧边栏（无 <li> 包裹），
            //   如本项目引擎自身 UI（src/frontend/templates/_layout_base.html）。
            //   根因(3层): ① 原逻辑仅提取 li，非 li 布局侧边栏 → 0 项 → 导航失败
            //   （"侧边栏中未找到 module=XX"）；② FullMenu hover/click 同样只遍历 li；
            //   ③ 被测系统多样性要求跨布局通用。
            //   修复: li 提取结果为空时，兜底提取容器内 a[href]/button 文本。
            if (items.length === 0) {
                for (const container of containers) {
                    const links = container.querySelectorAll('a[href], button');
                    for (const link of links) {
                        let text = (link.textContent || '').trim().replace(/\\s+/g, ' ');
                        // 跳过空/纯图标/过短文本
                        if (text && text.length >= 2 && text.length <= 50
                            && !seen.has(text)
                            && !/^[\\d\\s.\\-+*/×÷=<>()[\\]{}|&^%$#@!~`':;",?/\\\\]+$/.test(text)
                        ) {
                            seen.add(text);
                            items.push(text);
                        }
                    }
                    if (items.length >= 30) break;
                }
            }

            // ③ 提取弹出层/覆盖层中的菜单项（★ v10 2026-07-31: Portal渲染子菜单）
            //   根因(§10.9): Ant Design vertical模式子菜单通过Portal渲染到document.body下
            //   (.ant-menu-submenu-popup)，不在侧边栏容器内，导致JS提取遗漏。
            //   通用性: 覆盖所有框架的弹出层模式——Ant Design(.ant-menu-submenu-popup)、
            //   Element UI(.el-menu--popup)、以及通用popup/overlay/submenu类名。
            const popupSels = [
                '.ant-menu-submenu-popup',  // Ant Design vertical submenu portal
                '.el-menu--popup',          // Element UI popup menu
                '[class*="submenu-popup"]',
                '[class*="sub-menu-popup"]',
                '[class*="menu-popup"]',
                '[class*="menu__popup"]',
                '[role="menu"]',            // W3C ARIA menu (popup menus)
                '.ant-dropdown',            // Ant Design dropdown
                '.el-dropdown-menu',        // Element UI dropdown
                '[class*="dropdown-menu"]',
            ];
            for (const psel of popupSels) {
                try {
                    const pops = document.querySelectorAll(psel);
                    for (const pop of pops) {
                        const lis = pop.querySelectorAll('li');
                        for (const li of lis) {
                            _processLi(li);
                            if (items.length >= 50) break;
                        }
                        if (items.length >= 50) break;
                    }
                } catch(e) {}
                if (items.length >= 50) break;
            }

            // ④ 如果容器中没找到足够多，fallback到全页li（限制在body的非主要内容区）
            if (items.length < 3) {
                const allLis = document.querySelectorAll('body li');
                for (const li of allLis) {
                    _processLi(li);
                    if (items.length >= 30) break;
                }
            }

            return items;
        }
        """
        try:
            _items = await evaluate_bounded(page, _js_code)
            if isinstance(_items, list) and len(_items) > 0:
                _log.info(
                    "[FullMenu/JS] 提取到%d个菜单项: %s", len(_items), _items[:10]
                )
                return _items
            _log.warning("[FullMenu/JS] JS提取返回空列表")
            return []
        except Exception as _e:
            _log.warning("[FullMenu/JS] JS提取异常: %s", str(_e)[:120])
            return []

    async def _fallback_expand_and_capture(self, page, timeout_ms: int) -> list[str]:
        """Fallback: 使用Playwright定位器展开折叠区域并收集菜单项。

        仅在JS混合方案完全失败时使用（如页面不支持JS执行）。
        """
        import asyncio

        _all_items = []
        _seen = set()

        for _sel in [
            "[role='menuitem']",
            "[role='treeitem']",
            "nav li",
            "nav a",
            "[role='menu'] li",
            "[role='menu'] a",
            "aside li",
            "aside a",
        ]:
            with guard("_fallback_expand_and_capture: _locs = page.locator(_sel)", log=_log):
                _locs = page.locator(_sel)
                for _i in range(min(await count_bounded(_locs), 50)):
                    _txt = (
                        await await_bounded(
                            _locs.nth(_i).text_content(),
                            default="",
                            what="text_content",
                        )
                        or ""
                    ).strip()
                    if _txt and len(_txt) <= 50 and _txt not in _seen:
                        _seen.add(_txt)
                        _all_items.append(_txt)

        _expanded_count = 0
        with guard("_fallback_expand_and_capture: _subs = page.locator('[aria-expanded", log=_log, level=logging.WARNING):
            _subs = page.locator("[aria-expanded='false']")
            _cnt = await count_bounded(_subs)
            for _i in range(min(_cnt, 20)):
                with guard("_fallback_expand_and_capture: await _subs.nth(_i).click(timeout=30", log=_log):
                    await _subs.nth(_i).click(timeout=3000)
                    _expanded_count += 1
                    await asyncio.sleep(0.3)

        await asyncio.sleep(0.5)
        for _sel in [
            "[role='menuitem']",
            "[role='treeitem']",
            "nav li",
            "nav a",
            "[role='menu'] li",
            "[role='menu'] a",
            "aside li",
            "aside a",
        ]:
            with guard("_fallback_expand_and_capture: _locs = page.locator(_sel)", log=_log):
                _locs = page.locator(_sel)
                for _i in range(min(await count_bounded(_locs), 80)):
                    _txt = (
                        await await_bounded(
                            _locs.nth(_i).text_content(),
                            default="",
                            what="text_content",
                        )
                        or ""
                    ).strip()
                    if _txt and len(_txt) <= 50 and _txt not in _seen:
                        _seen.add(_txt)
                        _all_items.append(_txt)

        _log.info("[FullMenu/Fallback] 总计: %d项", len(_all_items))
        return _all_items

    async def _get_sidebar_items(self, page) -> list[str]:
        """获取侧边栏菜单项文本列表，用于页面内容验证时过滤干扰。

        通用性: 只用W3C ARIA属性+原生HTML标签，零框架特定类名。
        根因: 页面内容验证时全页快照含侧边栏文本，掩盖主内容区。
        用途: 在_guided_verify中过滤掉侧边栏文本，聚焦主内容区匹配。
        """
        _sidebar = []
        _seen = set()
        for _sel in [
            "[role='menuitem']",
            "[role='treeitem']",
            "nav li",
            "nav a",
            "[role='menu'] li",
            "[role='menu'] a",
        ]:
            with guard("_get_sidebar_items: _locs = page.locator(_sel)", log=_log):
                _locs = page.locator(_sel)
                _cnt = await count_bounded(_locs)
                for _i in range(min(_cnt, 80)):
                    _txt = (
                        await await_bounded(
                            _locs.nth(_i).text_content(),
                            default="",
                            what="text_content",
                        )
                        or ""
                    ).strip()
                    if _txt and len(_txt) <= 30 and _txt not in _seen:
                        _seen.add(_txt)
                        _sidebar.append(_txt)
        if _sidebar:
            _log.info("[SidebarItems] 提取%d个侧边栏菜单项用于过滤", len(_sidebar))
        return _sidebar
