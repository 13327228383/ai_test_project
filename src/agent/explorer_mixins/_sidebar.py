# -*- coding: utf-8 -*-
"""P2-10 SidebarMixin — 侧边栏/NavMap/Tab探索（~300行）。

通用性：不依赖静态DOM快照，实时浏览器交互。
跨系统/行业/页面适用。

[已复核-DeepSeek-V3 2026-08-16]
B4.3 硬编码清理：``_explore_nav_map_for_button`` 查 nav_graph_nodes 时原本把
项目名写死为某固定值，导致其他项目跨项目读到别人的导航图。已改为
运行时解析当前项目（见 ``_resolve_current_project``），解析不到则 fail-closed 跳过。
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any

from src.agent.element_utils import (
    click_button,
    try_pw_locate_button,
    get_page_snapshot,
    get_page_tabs,
    get_inpage_nav_links,
    match_keywords_to_elements,
)
from src.core.page_ready import await_bounded, count_bounded

_log = logging.getLogger(__name__)


def _resolve_current_project() -> str:
    """解析当前项目名，用于把 SQL 查询限定在本项目范围内。

    取值优先级：
      1. ``src.core.config_loader.config.project_name`` —— 每次 run 启动时
         ``config.reload(project, force=True)`` 会刷新，是流水线内最可靠的来源；
      2. ``src.backend.state._current_project()`` —— Web 请求/会话上下文兜底。

    Returns:
        项目名字符串；**任何一层都取不到时返回空串**，由调用方 fail-closed 处理
        （绝不回退到某个固定项目名，否则会造成跨项目数据串台）。
    """
    with guard("_resolve_current_project: from src.core.config_loader import c", log=_log):
        from src.core.config_loader import config as _cfg

        _name = (getattr(_cfg, "project_name", "") or "").strip()
        if _name:
            return _name
    with guard("_resolve_current_project: from src.backend.state import _curre", log=_log):
        from src.backend.state import _current_project as _get_project

        return (_get_project() or "").strip()
    return ""


class SidebarMixin:
    """侧边栏、NavMap、Tab 探索能力。

    提供：侧边栏菜单遍历、NavMap全局搜索、Tab页探索。
    策略：LLM语义排序 → 全遍历 → 子菜单递归 → Tab切换 → 导航回退。
    """

    async def _explore_sidebar_for_button(
        self,
        page,
        keywords: list[str],
        dom_buttons: list[dict],
        timeout_ms: int,
    ) -> dict[str, Any] | None:
        """侧边栏探索，全菜单项遍历 + 导航回退。

        策略:
          0. 展开所有折叠区域，LLM语义排序菜单项
          1. 遍历所有菜单项，点击→搜索目标按钮→失败则回退原页面
          2. 子菜单递归展开（多级菜单支持）
          3. Tab页探索（点击页面Tab查找按钮）
          通用性: 不依赖静态DOM快照，实时浏览器交互。
        """
        import asyncio

        _primary_kw = keywords[0] if keywords else ""
        _short_kw = _primary_kw[:4] if len(_primary_kw) >= 4 else _primary_kw
        _current_url = page.url

        # ★ 策略0: 展开所有折叠区域，获取完整菜单树
        _all_menu_texts = await self._expand_and_capture_full_menu(page, timeout_ms)

        # 合并 dom_buttons 中的菜单项（兜底）
        _sidebar_items = [
            b
            for b in dom_buttons
            if (b.get("role") in ("menuitem", "none") or b.get("tag") == "li")
            and b.get("text", "").strip()
        ]
        for _b in _sidebar_items:
            _t = _b.get("text", "").strip()
            if _t and _t not in _all_menu_texts:
                _all_menu_texts.append(_t)

        _log.info(
            "[SidebarExplore] 完整菜单项%d个, kw=%s",
            len(_all_menu_texts),
            _primary_kw[:30],
        )

        if not _all_menu_texts:
            _all_menu_texts = [
                b.get("text", "").strip()
                for b in dom_buttons
                if b.get("text", "").strip()
            ]
            _all_menu_texts = _all_menu_texts[:12]

        # ★ 策略0b: LLM语义排序全部菜单项（按与操作的关联度从高到低）
        _operation = keywords[0] if keywords else ""
        _sorted_items = await self._semantic_menu_rank(_operation, _all_menu_texts)
        if _sorted_items:
            _log.info("[SidebarExplore] LLM排序: %s", " → ".join(_sorted_items[:5]))
        else:
            _sorted_items = _all_menu_texts

        _explored = set()
        _skipped = []
        # ★ 通用修复(2026-07-28): 减少遍历范围 25→10，避免超时
        #   根因: 25项×8s/项=200s，加上LLM探索180s=380s>300s超时
        #   通用性: 跨系统/行业/页面 — LLM语义排序后前10项已覆盖最可能的目标
        for _item_text in _sorted_items[:10]:
            if not _item_text or _item_text in _explored:
                continue
            if len(_item_text) > 50:
                continue
            _explored.add(_item_text)

            _click_ok = await click_button(page, _item_text, min(timeout_ms, 15000))
            if not _click_ok:
                _skipped.append(_item_text[:20])
                continue

            with guard("_explore_sidebar_for_button: await page.wait_for_load_state('domc", log=_log):
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            await asyncio.sleep(1.5)

            _new_url = page.url
            if _new_url != _current_url:
                _log.info(
                    "[SidebarExplore] '%s'→URL变化: %s", _item_text[:20], _new_url[:60]
                )

            # ★ 策略1: Playwright locator 直接搜索目标按钮
            _pw_match = await try_pw_locate_button(page, _primary_kw, _short_kw)
            if _pw_match:
                _log.info(
                    "[SidebarExplore] ✅ PW '%s'→找到按钮: %r",
                    _item_text[:30],
                    _pw_match.get("element", {}).get("text", "")[:40],
                )
                return _pw_match

            # ★ 策略2: DOM snapshot 匹配
            _new_buttons, _new_inputs = await get_page_snapshot(page)
            _all_btns_text = [b.get("text", "")[:40] for b in _new_buttons[:10]]
            _log.debug(
                "[SidebarExplore] '%s'页面按钮: %s", _item_text[:20], _all_btns_text
            )
            _match = match_keywords_to_elements(keywords, _new_buttons)
            if _match and _match["score"] >= 5:
                _log.info(
                    "[SidebarExplore] ✅ DOM '%s'→找到按钮: %r score=%d",
                    _item_text[:30],
                    _match["element"].get("text", "")[:40],
                    _match["score"],
                )
                return _match

            # ★ 策略2b: 点击页面Tab查找按钮
            _tab_match = await self._explore_tabs_for_button(
                page, keywords, _new_buttons, timeout_ms
            )
            if _tab_match:
                return _tab_match

            # ★ 策略3: 子菜单递归展开（多级菜单，通用ARIA+原生标签）
            _sub_texts = []
            for _sel in ["[role='menuitem']", "[role='treeitem']", "nav li", "nav a"]:
                with guard("_explore_sidebar_for_button: _sub_locs = page.locator(_sel)", log=_log):
                    _sub_locs = page.locator(_sel)
                    for _i in range(min(await count_bounded(_sub_locs), 10)):
                        _t = (
                            await await_bounded(
                                _sub_locs.nth(_i).text_content(),
                                default="",
                                what="text_content",
                            )
                            or ""
                        ).strip()
                        if (
                            _t
                            and _t != _item_text
                            and _t not in _sub_texts
                            and len(_t) <= 50
                        ):
                            _sub_texts.append(_t)
            for _sub_text in _sub_texts[:5]:
                if _sub_text in _explored:
                    continue
                _explored.add(_sub_text)
                _sub_ok = await click_button(page, _sub_text, min(timeout_ms, 10000))
                if not _sub_ok:
                    continue
                await asyncio.sleep(1)
                _pw_sub = await try_pw_locate_button(page, _primary_kw, _short_kw)
                if _pw_sub:
                    _log.info(
                        "[SidebarExplore] ✅ Sub '%s'→'%s'→找到按钮: %r",
                        _item_text[:20],
                        _sub_text[:20],
                        _pw_sub.get("element", {}).get("text", "")[:40],
                    )
                    return _pw_sub
                _sub_btns, _ = await get_page_snapshot(page)
                _sub_match = match_keywords_to_elements(keywords, _sub_btns)
                if _sub_match and _sub_match["score"] >= 5:
                    return _sub_match

            # ★ 策略4: 导航回退到原页面（URL变化但未找到按钮时）
            if _new_url != _current_url:
                with guard("_explore_sidebar_for_button: await page.goto(_current_url, timeou", log=_log):
                    await page.goto(_current_url, timeout=15000)
                    await page.wait_for_load_state("domcontentloaded", timeout=5000)
                    await asyncio.sleep(1)

        if _skipped:
            _log.info(
                "[SidebarExplore] 跳过%d个菜单项(点击失败): %s",
                len(_skipped),
                _skipped[:5],
            )
        return None

    async def _explore_tabs_for_button(
        self,
        page,
        keywords: list[str],
        dom_buttons: list[dict],
        timeout_ms: int,
    ) -> dict[str, Any] | None:
        """点击页面Tab查找目标按钮（通用性：处理Tab页面内容切换）。

        ★ 通用性修复(2026-07-28): 使用独立的get_page_tabs()获取Tab元素，
        而非从dom_buttons过滤（dom_buttons已移除[role=tab]选择器）。
        """
        import asyncio

        # ★ 优先使用独立函数获取Tab（dom_buttons中已无Tab元素）
        _tabs = await get_page_tabs(page)
        if not _tabs:
            # 兜底：从dom_buttons过滤（兼容旧调用方）
            _tabs = [
                b
                for b in dom_buttons
                if b.get("role") == "tab" and b.get("text", "").strip()
            ]
        if not _tabs:
            return None
        _primary_kw = keywords[0] if keywords else ""
        _short_kw = _primary_kw[:4] if len(_primary_kw) >= 4 else _primary_kw
        _log.info("[TabExplore] 探索%d个Tab, kw=%s", len(_tabs), _primary_kw[:30])
        for _tab in _tabs[:6]:
            _tab_text = _tab.get("text", "").strip()
            if not _tab_text or len(_tab_text) > 20:
                continue
            _click_ok = await click_button(page, _tab_text, min(timeout_ms, 10000))
            if not _click_ok:
                continue
            await asyncio.sleep(0.8)
            _pw_match = await try_pw_locate_button(page, _primary_kw, _short_kw)
            if _pw_match:
                _log.info(
                    "[TabExplore] Tab '%s'→找到按钮: %r",
                    _tab_text[:20],
                    _pw_match.get("element", {}).get("text", "")[:40],
                )
                return _pw_match
            _tab_btns, _ = await get_page_snapshot(page)
            _match = match_keywords_to_elements(keywords, _tab_btns)
            if _match and _match["score"] >= 5:
                return _match
        return None

    async def _explore_inpage_nav_for_button(
        self,
        page,
        keywords: list[str],
        timeout_ms: int,
    ) -> dict[str, Any] | None:
        """点击页内导航链接查找目标按钮（通用性：处理页内二级导航）。

        ★ 通用性修复(2026-07-29): 新增策略。
        根因(5层why):
          1. TC-REQ-001-I01超时 → "数据导入"按钮在"物料管理"子页面
          2. 子页面通过页内导航到达 → 需要点击"物料管理"链接
          3. Agent不探索页内导航 → 只探索Tab和侧边栏
          4. 页内导航未被捕获 → 缺少通用检测机制
          5. 缺少探索策略 → Agent永远找不到页内子页面中的按钮
        通用性: 跨系统/行业/页面 — 所有Web应用都可能有页内二级导航
        （如Ant Design Pro的PageContainer tabList、自定义TabBar、水平导航等）
        """
        import asyncio
        from urllib.parse import urlparse as _urlparse

        _nav_links = await get_inpage_nav_links(page)
        if not _nav_links:
            return None
        # 排除取消/关闭/删除类破坏性候选：点击会把当前表单页导航走（实测 UX02
        # '取消' 使 /assets/new 跳回列表页，后续录入断言全部落空）。取消/删除词集
        # 由 Step3 _SEMANTIC_CATEGORIES 唯一权威，本层只读不新造。
        _destructive_kws: frozenset[str] = frozenset()
        with guard("_explore_inpage_nav_for_button: from src.pipeline.step3._rule_extrac", log=_log):
            from src.pipeline.step3._rule_extractor_buttons import (
                _SEMANTIC_CATEGORIES as _nav_semantic,
            )

            _destructive_kws = frozenset(
                _w
                for _cat in ("cancel", "delete")
                for _w in _nav_semantic.get(_cat, ())
            )
        if _destructive_kws:
            _pre_len = len(_nav_links)
            _nav_links = [
                _n
                for _n in _nav_links
                if (_n.get("text") or "").strip() not in _destructive_kws
            ]
            if len(_nav_links) != _pre_len:
                _log.info(
                    "[InPageNav] 排除%d个破坏性候选: %s",
                    _pre_len - len(_nav_links),
                    sorted(_destructive_kws)[:6],
                )
        if not _nav_links:
            return None
        _primary_kw = keywords[0] if keywords else ""
        _short_kw = _primary_kw[:4] if len(_primary_kw) >= 4 else _primary_kw
        _log.info(
            "[InPageNav] 探索%d个页内导航链接, kw=%s", len(_nav_links), _primary_kw[:30]
        )
        # ★ v5.12 fix(2026-07-31): 保存原始URL，检测页面跳转
        _original_url = page.url
        _original_path = _urlparse(_original_url).path.rstrip("/")
        for _nav in _nav_links[:8]:  # 限制8个，防止超时
            _nav_text = _nav.get("text", "").strip()
            if not _nav_text:
                continue
            _click_ok = await click_button(page, _nav_text, min(timeout_ms, 8000))
            if not _click_ok:
                continue
            await asyncio.sleep(0.8)
            # ★ v5.18 fix(2026-07-31): 按钮搜索移到URL跳转检测之前
            #   根因(§10.9 5层why):
            #     L1: TC-REQ-001-I01找不到"数据导入"按钮
            #     L2: 按钮不在仪表盘页面，在子页面中
            #     L3: InPageNav点击仪表盘卡片(产品管理/配方管理等)后URL必然变化
            #     L4: v5.12 URL跳转检测在按钮搜索之前，URL变化后立即回退+continue
            #     L5: 按钮搜索代码(lines 261-270)从未被执行——所有子页面导航都被跳过
            #   修复: 先搜索按钮(找到就返回)，找不到再检查URL变化并回退
            #   通用性: 跨系统/行业/框架 — 所有Web应用的导航都会改变URL，
            #           Agent应探索目标页面再决定是否回退，而非盲目回退
            # ① 等待子页面内容加载
            with guard("_explore_inpage_nav_for_button: await page.wait_for_load_state('domc", log=_log):
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            await asyncio.sleep(0.3)
            # ② 搜索按钮：PW定位 → 完整快照关键词匹配
            _pw_match = await try_pw_locate_button(page, _primary_kw, _short_kw)
            if _pw_match:
                _log.info(
                    "[InPageNav] '%s'→找到按钮(PW): %r",
                    _nav_text[:20],
                    _pw_match.get("element", {}).get("text", "")[:40],
                )
                return _pw_match
            _nav_btns, _ = await get_page_snapshot(page)
            _match = match_keywords_to_elements(keywords, _nav_btns)
            if _match and _match["score"] >= 5:
                _log.info("[InPageNav] '%s'→关键词匹配成功", _nav_text[:20])
                return _match
            # ③ 按钮未找到，检查URL是否变化，变化则回退原页面
            _new_url = page.url
            _new_path = _urlparse(_new_url).path.rstrip("/")
            if _new_path and _original_path and _new_path != _original_path:
                _log.info(
                    "[InPageNav] '%s'→按钮未找到+URL跳转(%s→%s)，回退",
                    _nav_text[:20],
                    _original_path[-40:],
                    _new_path[-40:],
                )
                with guard("_explore_inpage_nav_for_button: for _attempt in (1, 2): await page.g", log=_log):
                    for _attempt in (1, 2):
                        await page.goto(_original_url, timeout=min(timeout_ms, 10000))
                        await asyncio.sleep(0.6)
                        # 校验回退真的回到原路径；SPA 路由可能拦截首次 goto，
                        # 重试一次避免"回退不彻底"（实测 UX02 回退后仍停留列表页）
                        _back_path = _urlparse(page.url).path.rstrip("/")
                        if not _back_path or _back_path == _original_path:
                            break
        return None

    async def _explore_nav_map_for_button(
        self,
        page,
        keywords: list[str],
        timeout_ms: int,
    ) -> dict[str, Any] | None:
        """nav_map 全局搜索兜底。

        当侧边栏探索失败时，遍历 nav_graph_nodes 中所有页面搜索目标按钮。
        通用性: 跨系统/行业/页面 — 不依赖静态爬取完整性。
        策略: 优先匹配 URL 路径与 operation 关键词相关的页面，减少导航次数。
        """
        import asyncio, re as _re, pymysql.cursors as _pmc

        _primary_kw = keywords[0] if keywords else ""
        _short_kw = _primary_kw[:4] if len(_primary_kw) >= 4 else _primary_kw

        _log.info("[NavMapExplore] 开始nav_map全局搜索: kw=%s", _primary_kw[:30])

        # ★ [B4.3 硬编码清理] 原 SQL 参数写死为某固定项目名：任何其他项目都会去读
        #   该项目的导航图 —— 要么查不到（兜底失效），要么拿到**别的项目的页面 URL**
        #   然后真的导航过去，属于跨项目数据串台。改为取当前项目。
        _project = _resolve_current_project()
        if not _project:
            # fail-closed：宁可不兜底，也绝不拿错项目的导航图去跑
            _log.warning("[NavMapExplore] 无法确定当前项目，跳过 nav_map 全局搜索")
            return None

        try:
            from src.core.db_manager import get_connection

            _conn = get_connection()
            _raw = _conn._conn._con
            _cur = _raw.cursor(_pmc.Cursor)
            _cur.execute(
                "SELECT DISTINCT page_url FROM nav_graph_nodes WHERE project=%s ORDER BY page_url",
                (_project,),
            )
            _all_urls = [r[0] for r in _cur.fetchall() if r[0]]
            _conn.close()
        except Exception as _e:
            _log.warning("[NavMapExplore] 查询nav_map失败: %s", str(_e)[:80])
            return None

        if not _all_urls:
            _log.warning("[NavMapExplore] nav_map为空")
            return None

        # 优先排序：URL路径含operation关键词的页面排在前面
        _kw_chars = _re.findall(r"[\u4e00-\u9fff]", _primary_kw)
        _kw2 = "".join(_kw_chars[:2]) if len(_kw_chars) >= 2 else ""

        def _url_score(url: str) -> int:
            _s = 0
            if _kw2 and _kw2 in url:
                _s += 10
            if _primary_kw[:2] in url:
                _s += 5
            return _s

        _sorted_urls = sorted(_all_urls, key=_url_score, reverse=True)
        _current_url = (page.url or "").strip()
        _searched = 0

        _log.info(
            "[NavMapExplore] 共%d个页面, 优先搜索%d个相关页面",
            len(_sorted_urls),
            sum(1 for u in _sorted_urls if _url_score(u) > 0),
        )

        for _url in _sorted_urls[:15]:
            if _url == _current_url:
                continue
            _searched += 1
            try:
                await page.goto(
                    _url, timeout=min(timeout_ms, 15000), wait_until="domcontentloaded"
                )
                await asyncio.sleep(1.5)
            except Exception:
                continue

            _pw_match = await try_pw_locate_button(page, _primary_kw, _short_kw)
            if _pw_match:
                _log.info(
                    "[NavMapExplore] ✅ '%s'→找到按钮: %r",
                    _url[:60],
                    _pw_match.get("element", {}).get("text", "")[:40],
                )
                return _pw_match

            _new_btns, _ = await get_page_snapshot(page)
            _match = match_keywords_to_elements(keywords, _new_btns)
            if _match and _match["score"] >= 5:
                _log.info(
                    "[NavMapExplore] ✅ '%s'→找到按钮: %r score=%d",
                    _url[:60],
                    _match["element"].get("text", "")[:40],
                    _match["score"],
                )
                return _match

        _log.info(
            "[NavMapExplore] ❌ 遍历%d个页面未找到按钮: %s", _searched, _primary_kw[:30]
        )
        return None
