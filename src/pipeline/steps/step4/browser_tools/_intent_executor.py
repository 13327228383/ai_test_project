# -*- coding: utf-8 -*-
"""意图定位执行层模块 — 从 locator.py 拆分（§4.3 单文件≤800行）。

职责：导航+扫描按钮（async函数）。
依赖：_intent_data（数据查询）+ _basic_locator（_find_visible_modal）。
"""
import logging

from src.pipeline.steps.step4.browser_tools._intent_data import (
    _extract_domain_keywords,
    _find_button_page_in_rag,
    _load_nav_map_pages,
    _load_rag_pages,
)
from src.pipeline.steps.step4.browser_tools._basic_locator import _find_visible_modal

_log = logging.getLogger(__name__)


async def _navigate_to_page_and_find_button(
    page, rag_url: str, operation: str, domain_kws: list[str] = None
):
    """★ v3 通用修复 (ADR-017): 导航到rag页面+Tab切换+找按钮。

    通用性(§12.4):
      - 跨系统: Tab选择器覆盖Ant Design/ElementUI/Naive UI/原生HTML
      - 跨行业: 域关键词从operation动态提取，无业务词
      - 跨Tab: URL含tab=时先导航再点Tab(兼容React Router和Ant Design两种Tab实现)
    """
    try:
        from src.pipeline.step3._rule_extractor import _match_button_by_keywords
    except ImportError as _e:
        _log.debug(
            "[Step4] import _match_button_by_keywords 失败(§5+§12.5 Step4越界引用Step3): %s",
            _e,
        )
        return None

    import re as _re_nav

    _nav_url = rag_url
    if _nav_url.startswith("/"):
        try:
            from src.core.config_loader import config

            _base = (getattr(config, "target_url", "") or "").strip().rstrip("/")
            if _base:
                _nav_url = _base + _nav_url
        except Exception as _se10:
            _log.debug("[locator] 异常降级(pass): %s", str(_se10)[:60], exc_info=True)
            pass
    if not _nav_url.startswith(("http://", "https://")):
        _log.warning("[_navigate_to_page_and_find_button] URL无效: %s", _nav_url[:80])
        return None

    try:
        await page.goto(_nav_url, wait_until="domcontentloaded", timeout=15000)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception as _se11:
            _log.debug("[locator] 异常降级(pass): %s", str(_se11)[:60], exc_info=True)
            pass
        await page.wait_for_timeout(1500)
    except Exception as _e:
        _log.warning(
            "[_navigate_to_page_and_find_button] 导航失败: %s err=%s",
            rag_url[:60],
            str(_e)[:80],
        )
        return None

    async def _scan_buttons():
        _texts, _locators = [], {}
        for _sel in [
            "button:visible",
            "[role='button']:visible",
            "a:visible",
            "input[type='submit']:visible",
            "input[type='button']:visible",
        ]:
            try:
                _locs = page.locator(_sel)
                _cnt = await _locs.count()
                for _i in range(min(_cnt, 50)):
                    try:
                        _loc = _locs.nth(_i)
                        _txt = (await _loc.text_content() or "").strip()
                        if _txt and _txt not in _texts:
                            _texts.append(_txt)
                            _locators[_txt] = _loc
                    except Exception as _se12:
                        _log.debug(
                            "[locator] 异常降级(continue): %s",
                            str(_se12)[:60],
                            exc_info=True,
                        )
                        continue
            except Exception as _se13:
                _log.debug(
                    "[locator] 异常降级(continue): %s", str(_se13)[:60], exc_info=True
                )
                continue
        return _texts, _locators

    _new_texts, _new_locators = await _scan_buttons()
    if _new_texts:
        _matched_new = _match_button_by_keywords(operation, _new_texts)
        if _matched_new and _matched_new in _new_locators:
            # ★ v4.1 通用修复 (ADR-017, 2026-07-23): 域关键词验证
            #   根因(§10.9): "单个编辑辅材"匹配到模型管理页"修改配置"(都是edit类但域不同)
            #   修复: 匹配到按钮后检查页面是否含operation域关键词，不含则拒绝
            #   通用性: 域关键词从operation动态提取，无业务硬编码
            _domain_ok = True
            if domain_kws:
                try:
                    _page_text = await page.text_content("body", timeout=3000) or ""
                    _domain_score = sum(1 for kw in domain_kws if kw in _page_text)
                    if _domain_score == 0:
                        _log.info(
                            "[_navigate_to_page_and_find_button] v4.1 域不匹配: op=%r btn=%r kws=%s → 拒绝",
                            operation[:30],
                            _matched_new[:30],
                            domain_kws[:5],
                        )
                        _domain_ok = False
                except Exception as _se14:
                    _log.debug(
                        "[locator] 异常降级(pass): %s", str(_se14)[:60], exc_info=True
                    )
                    pass
            if _domain_ok:
                _log.info(
                    "[_navigate_to_page_and_find_button] 导航后命中: op=%r → btn=%r",
                    operation[:30],
                    _matched_new[:30],
                )
                return _new_locators[_matched_new]

    # ★ Tab切换: URL含tab=或当前页有Tab元素 → 点Tab切换找按钮
    #   v4.1: 放宽Tab切换条件 — 不再要求URL含tab=，只要页面有Tab元素就尝试
    #   根因: /dataManage/portraitData 不含tab=参数但有Tab元素(辅材配方/叶组配方等)
    #   通用: 覆盖Ant Design(.ant-tabs-tab)/ElementUI(.el-tabs__item)/Naive UI(.n-tabs-tab)/ARIA(role=tab)
    _has_tab = False
    for _ts_check in [
        "[role='tab']:visible",
        ".ant-tabs-tab:visible",
        ".el-tabs__item:visible",
    ]:
        try:
            if await page.locator(_ts_check).count() > 0:
                _has_tab = True
                break
        except Exception as _se15:
            _log.debug(
                "[locator] 异常降级(continue): %s", str(_se15)[:60], exc_info=True
            )
            continue
    if domain_kws and _has_tab:
        _log.info(
            "[_navigate_to_page_and_find_button] 尝试Tab切换: kws=%s url=%s",
            domain_kws[:5],
            rag_url[:50],
        )
        _tab_sels = [
            "[role='tab']:visible",
            ".ant-tabs-tab:visible",
            ".el-tabs__item:visible",
            ".n-tabs-tab:visible",
            ".ant-tabs-tab-btn:visible",
        ]
        for _ts in _tab_sels:
            try:
                _tabs = page.locator(_ts)
                _tcnt = await _tabs.count()
                for _ti in range(min(_tcnt, 20)):
                    try:
                        _tloc = _tabs.nth(_ti)
                        _ttxt = (await _tloc.text_content() or "").strip()
                        if not _ttxt:
                            continue
                        _tab_match = any(kw in _ttxt for kw in domain_kws)
                        if _tab_match:
                            _log.info(
                                "[_navigate_to_page_and_find_button] 点Tab: '%s' (sel=%s)",
                                _ttxt[:30],
                                _ts[:30],
                            )
                            await _tloc.click(timeout=5000)
                            await page.wait_for_timeout(2000)
                            _new_texts, _new_locators = await _scan_buttons()
                            if _new_texts:
                                _matched_new = _match_button_by_keywords(
                                    operation, _new_texts
                                )
                                if _matched_new and _matched_new in _new_locators:
                                    _log.info(
                                        "[_navigate_to_page_and_find_button] Tab切换后命中: op=%r → btn=%r",
                                        operation[:30],
                                        _matched_new[:30],
                                    )
                                    return _new_locators[_matched_new]
                            break
                    except Exception as _se16:
                        _log.debug(
                            "[locator] 异常降级(continue): %s",
                            str(_se16)[:60],
                            exc_info=True,
                        )
                        continue
                if _new_texts:
                    break
            except Exception as _se17:
                _log.debug(
                    "[locator] 异常降级(continue): %s", str(_se17)[:60], exc_info=True
                )
                continue

    _log.warning(
        "[_navigate_to_page_and_find_button] 未找到按钮: op=%r url=%r",
        operation[:30],
        rag_url[:60],
    )


async def _find_button_via_nav_map(page, operation: str, domain_kws: list):
    """★ v4 通用修复 (ADR-017, 2026-07-23): nav_map 兜底 — 导航到最相关页面找按钮。
    ★ v4.4 修复 (2026-07-23): 合并 nav_map + rag_chunks 双数据源，补充未入 nav_map 的 Tab 子页面。

    当 rag_chunks 找不到匹配按钮时，从 nav_map + rag_chunks 获取所有页面URL，
    用域关键词评分选择最相关页面，导航后实时扫描按钮(含表格行内)。

    根因(§10.9):
      1. 表层: nav_map 只有16个基础页面，缺 ?menuKey=formula 子菜单页面
      2. 中层: Step1 Phase2 _synthetic_tab_url 返回 None → Tab快照未入 nav_map
      3. 根因: nav_map 数据不完整，但 rag_chunks 包含所有访问过的页面(含Tab子页面)
      4. 全局: 合并两个数据源，rag_chunks 补充 nav_map 缺失的页面

    通用性(§12.4):
      - 跨系统: 复用 _navigate_to_page_and_find_button(含Tab切换)
      - 跨行业: 域关键词从operation动态提取
      - 跨页面: 遍历 nav_map + rag_chunks 所有页面
      - 数据完整性: 两个数据源都为空时返回None，调用方降级到Tab探索
      - 根因溯源: 在Step4职责边界内补充数据源，不越界到Step1
    """
    if not operation:
        return None
    try:
        from src.pipeline.step3._rule_extractor import _match_button_by_keywords
    except ImportError as _e:
        _log.debug(
            "[Step4] import _match_button_by_keywords 失败(§5+§12.5 Step4越界引用Step3): %s",
            _e,
        )
        return None

    _project = ""
    try:
        from src.core.config_loader import config

        _project = getattr(config, "project_name", "") or ""
    except Exception as _se18:
        _log.debug("[locator] 异常降级(pass): %s", str(_se18)[:60], exc_info=True)
        pass
    if not _project:
        try:
            from src.backend.state import _current_project

            _project = _current_project() or ""
        except Exception as _se19:
            _log.debug("[locator] 异常降级(pass): %s", str(_se19)[:60], exc_info=True)
            pass
    if not _project:
        return None

    # ★ v4.4: 合并 nav_map + rag_chunks 双数据源
    #   nav_map 优先(URL更完整，有 real_url)，rag_chunks 补充(含Tab子页面)
    _nav_pages = _load_nav_map_pages(_project)
    _rag_pages = _load_rag_pages(_project)
    _pages = list(_nav_pages)
    _seen_paths = set(p["path"] for p in _nav_pages)
    for _rp in _rag_pages:
        if _rp["path"] not in _seen_paths:
            _pages.append(_rp)
            _seen_paths.add(_rp["path"])
    if not _pages:
        _log.info(
            "[_find_button_via_nav_map] nav_map(%d)+rag(%d) 均为空，跳过",
            len(_nav_pages),
            len(_rag_pages),
        )
        return None

    # 域关键词评分：选择最可能含目标按钮的页面
    # 通用: 域关键词从operation提取(如"辅材"→匹配含"辅材"的页面text/path)
    # ★ v4.4: rag_chunks 页面的 text 含 DOM 文本，域关键词匹配更精准
    _scored_pages = []
    for _pg in _pages:
        _score = 0
        _page_ctx = (_pg.get("text", "") + " " + _pg.get("path", "")).lower()
        for _kw in domain_kws or []:
            if _kw and _kw in _page_ctx:
                _score += 2
        # 含 tab= 或 menuKey= 的页面优先(Tab/子菜单页往往有更多操作按钮)
        if "tab=" in _pg.get("path", "") or "#tab=" in _pg.get("path", ""):
            _score += 1
        if "menukey=" in _pg.get("path", "").lower():
            _score += 1
        _scored_pages.append((_pg, _score))
    # 按评分降序，评分相同的按原顺序
    _scored_pages.sort(key=lambda x: -x[1])

    _log.info(
        "[_find_button_via_nav_map] v4.4: op=%r 域kws=%s → nav_map=%d+rag=%d=%d页面, top3=%s",
        operation[:30],
        (domain_kws or [])[:5],
        len(_nav_pages),
        len(_rag_pages),
        len(_scored_pages),
        [(p[0]["path"][:25], p[1]) for p in _scored_pages[:3]],
    )

    # 最多尝试前8个页面(评分最高的)，避免遍历全部导致超时
    # ★ v4.1: 去掉score=0跳过逻辑 — domain_kws可能无法匹配页面名(如"辅材"不在任何页面名中)，
    #   但页面内可能有Tab含域关键词。让前8个页面都被尝试，域关键词验证会拒绝错误页面。
    # ★ v4.4: 从5提升到8 — rag_chunks 补充的页面可能在评分末尾但含目标Tab
    for _pg, _score in _scored_pages[:8]:
        _nav_loc = await _navigate_to_page_and_find_button(
            page, _pg["url"], operation, domain_kws
        )
        if _nav_loc is not None:
            _log.info(
                "[_find_button_via_nav_map] 命中: op=%r page=%r score=%d",
                operation[:30],
                _pg["path"][:30],
                _score,
            )
            return _nav_loc

    return None


async def _find_button_via_tab_exploration(page, operation: str, domain_kws: list):
    """★ v4 通用修复 (ADR-017, 2026-07-23): 当前页 Tab 实时探索 — 逐个点Tab找按钮。

    根因(§10.9):
      1. nav_map 缺"辅材配方"Tab → nav_map兜底也找不到
      2. 但目标系统页面上实际存在"辅材配方"Tab元素(只是没被Step1爬到)
      3. 实时探索: 扫描当前页所有Tab元素 → 逐个点击 → 每次扫描按钮

    通用性(§12.4):
      - 跨系统: Tab选择器覆盖Ant Design/ElementUI/Naive UI/ARIA
      - 跨行业: 域关键词从operation提取，匹配Tab文本
      - 跨Tab: 正是解决跨Tab按钮找不到的问题
      - 跨页面: 适用于所有含Tab的页面
    """
    if not operation:
        return None
    try:
        from src.pipeline.step3._rule_extractor import _match_button_by_keywords
    except ImportError as _e:
        _log.debug(
            "[Step4] import _match_button_by_keywords 失败(§5+§12.5 Step4越界引用Step3): %s",
            _e,
        )
        return None

    # 1. 扫描当前页所有 Tab 元素
    _tab_sels = [
        "[role='tab']:visible",
        ".ant-tabs-tab:visible",
        ".el-tabs__item:visible",
        ".n-tabs-tab:visible",
        ".ant-tabs-tab-btn:visible",
        ".el-tabs__nav-wrap .el-tabs__item:visible",
    ]
    _tab_locators = []  # [(text, locator)]
    _seen_tab_texts = set()
    for _ts in _tab_sels:
        try:
            _tabs = page.locator(_ts)
            _tcnt = await _tabs.count()
            for _ti in range(min(_tcnt, 20)):
                try:
                    _tloc = _tabs.nth(_ti)
                    _ttxt = (await _tloc.text_content() or "").strip()
                    if _ttxt and _ttxt not in _seen_tab_texts:
                        _seen_tab_texts.add(_ttxt)
                        _tab_locators.append((_ttxt, _tloc))
                except Exception as _se20:
                    _log.debug(
                        "[locator] 异常降级(continue): %s",
                        str(_se20)[:60],
                        exc_info=True,
                    )
                    continue
        except Exception as _se21:
            _log.debug(
                "[locator] 异常降级(continue): %s", str(_se21)[:60], exc_info=True
            )
            continue

    if not _tab_locators:
        _log.debug(
            "[_find_button_via_tab_exploration] 当前页无Tab元素: op=%r", operation[:30]
        )
        return None

    _log.info(
        "[_find_button_via_tab_exploration] op=%r 发现%d个Tab: %s",
        operation[:30],
        len(_tab_locators),
        [t[0][:10] for t in _tab_locators[:8]],
    )

    # 2. 用域关键词对Tab文本评分，优先点击最相关的Tab
    _domain_kws = domain_kws or []
    _scored_tabs = []
    for _ttxt, _tloc in _tab_locators:
        _tscore = sum(1 for kw in _domain_kws if kw and kw in _ttxt)
        _scored_tabs.append((_ttxt, _tloc, _tscore))
    _scored_tabs.sort(key=lambda x: -x[2])

    # 3. 逐个点击Tab + 扫描按钮
    for _ttxt, _tloc, _tscore in _scored_tabs:
        try:
            _log.info(
                "[_find_button_via_tab_exploration] 点Tab: '%s' (score=%d)",
                _ttxt[:20],
                _tscore,
            )
            await _tloc.click(timeout=5000)
            await page.wait_for_timeout(2000)
            # 扫描按钮(复用 _navigate_to_page_and_find_button 的 _scan_buttons 逻辑)
            _btn_texts = []
            _btn_locators = {}
            for _sel in [
                "button:visible",
                "[role='button']:visible",
                "a:visible",
                "input[type='submit']:visible",
                "input[type='button']:visible",
            ]:
                try:
                    _locs = page.locator(_sel)
                    _cnt = await _locs.count()
                    for _i in range(min(_cnt, 50)):
                        try:
                            _loc = _locs.nth(_i)
                            _txt = (await _loc.text_content() or "").strip()
                            if _txt and _txt not in _btn_texts:
                                _btn_texts.append(_txt)
                                _btn_locators[_txt] = _loc
                        except Exception as _se22:
                            _log.debug(
                                "[locator] 异常降级(continue): %s",
                                str(_se22)[:60],
                                exc_info=True,
                            )
                            continue
                except Exception as _se23:
                    _log.debug(
                        "[locator] 异常降级(continue): %s",
                        str(_se23)[:60],
                        exc_info=True,
                    )
                    continue
            if _btn_texts:
                _matched = _match_button_by_keywords(operation, _btn_texts)
                if _matched and _matched in _btn_locators:
                    # ★ v4.2 通用修复 (ADR-017, 2026-07-23): Tab探索后域验证
                    #   根因(§10.9): v4.1只在_navigate_to_page_and_find_button加了域验证，
                    #     遗漏了_find_button_via_tab_exploration → Tab切换后匹配到错误按钮
                    #     (如"修改配置"匹配"单个编辑辅材"→假命中)
                    #   通用性(§12.4): 与v4.1域验证逻辑一致，覆盖所有Tab探索场景
                    #   覆盖性: 补齐intent:定位5层fallback中最后一层的域验证缺口
                    _domain_ok = True
                    if domain_kws:
                        try:
                            _page_text = (
                                await page.text_content("body", timeout=3000) or ""
                            )
                            _domain_score = sum(
                                1 for kw in domain_kws if kw in _page_text
                            )
                            if _domain_score == 0:
                                _log.info(
                                    "[_find_button_via_tab_exploration] v4.2 域不匹配: op=%r btn=%r tab=%r kws=%s → 拒绝",
                                    operation[:30],
                                    _matched[:30],
                                    _ttxt[:15],
                                    domain_kws[:5],
                                )
                                _domain_ok = False
                        except Exception as _se24:
                            _log.debug(
                                "[locator] 异常降级(pass): %s",
                                str(_se24)[:60],
                                exc_info=True,
                            )
                            pass
                    if _domain_ok:
                        _log.info(
                            "[_find_button_via_tab_exploration] Tab'%s'后命中: op=%r → btn=%r",
                            _ttxt[:15],
                            operation[:30],
                            _matched[:30],
                        )
                        return _btn_locators[_matched]
        except Exception as _e:
            _log.debug(
                "[_find_button_via_tab_exploration] 点Tab异常: '%s' err=%s",
                _ttxt[:15],
                str(_e)[:60],
            )
            continue

    return None


async def _locate_by_intent(page, operation: str, kind: str = "click"):
    """★ 方案B (2026-07-23): 实时在页面DOM中找匹配operation语义的按钮。

    Ego Lite 思路：不依赖预爬取数据，实时看页面找按钮。
    流程：
      1. 实时获取页面所有按钮文本（button / [role=button] / a / input[type=submit]）
      2. 用 _match_button_by_keywords 做语义匹配（复用Step3逻辑，无业务硬编码）
      3. 匹配到 → 返回 Locator
      4. 匹配不到 → 检查 modal 内按钮（弹窗场景）
      5. 还找不到 → 返回 None（由调用方走 AI fallback）

    通用性:
      - 跨系统: button/[role=button]/a/input[type=submit] 覆盖所有UI框架
      - 跨行业: _match_button_by_keywords 纯语义匹配，无业务词
      - 跨页面: 实时读取当前DOM，Tab/弹窗/懒加载均适用
    """
    if not operation:
        return None
    try:
        from src.pipeline.step3._rule_extractor import _match_button_by_keywords
    except ImportError as _e:
        _log.debug(
            "[Step4] import _match_button_by_keywords 失败(§5+§12.5 Step4越界引用Step3): %s",
            _e,
        )
        return None

    try:
        # 1. 实时获取页面所有可见按钮文本
        _btn_texts = []
        _btn_locators = {}  # text → locator
        _selectors = [
            "button:visible",
            "[role='button']:visible",
            "a:visible",
            "input[type='submit']:visible",
            "input[type='button']:visible",
        ]
        for _sel in _selectors:
            try:
                _locs = page.locator(_sel)
                _cnt = await _locs.count()
                for _i in range(min(_cnt, 50)):  # 每类最多扫50个，防超时
                    try:
                        _loc = _locs.nth(_i)
                        _txt = (await _loc.text_content() or "").strip()
                        if _txt and _txt not in _btn_texts:
                            _btn_texts.append(_txt)
                            _btn_locators[_txt] = _loc
                    except Exception as _se25:
                        _log.debug(
                            "[locator] 异常降级(continue): %s",
                            str(_se25)[:60],
                            exc_info=True,
                        )
                        continue
            except Exception as _se26:
                _log.debug(
                    "[locator] 异常降级(continue): %s", str(_se26)[:60], exc_info=True
                )
                continue

        if not _btn_texts:
            _log.info(
                "[_locate_by_intent] 当前页无可见按钮: operation=%r → 直接走rag_chunks辅助",
                operation[:30],
            )
            _matched = None
        else:
            _matched = _match_button_by_keywords(operation, _btn_texts)
        if _matched and _matched in _btn_locators:
            # ★ v3 通用修复 (ADR-017, 2026-07-23): 域关键词上下文检查
            #   根因(§10.9): "批量导入辅材"在产品页匹配到"数据导入"(错误页)而非辅材配方Tab页
            #   修复: 找到按钮后检查当前页DOM是否含operation域关键词，不含则查rag找更好页面
            #   通用性: 域关键词从operation动态提取，无业务硬编码
            _domain_kws = _extract_domain_keywords(operation)
            if _domain_kws:
                try:
                    _page_text = await page.text_content("body", timeout=3000) or ""
                except Exception:
                    _page_text = ""
                _current_score = sum(1 for kw in _domain_kws if kw in _page_text)
                if _current_score == 0:
                    _log.info(
                        "[_locate_by_intent] v3 域上下文不匹配: op=%r kws=%s current_score=0 → 查rag",
                        operation[:30],
                        _domain_kws[:5],
                    )
                    _rag_result = _find_button_page_in_rag(operation)
                    if _rag_result:
                        _rag_url, _rag_btn = _rag_result
                        _log.info(
                            "[_locate_by_intent] v3 rag找到更好页面: page=%r btn=%r",
                            _rag_url[:60],
                            _rag_btn[:30],
                        )
                        _nav_loc = await _navigate_to_page_and_find_button(
                            page, _rag_url, operation, _domain_kws
                        )
                        if _nav_loc is not None:
                            return _nav_loc
                        _log.warning(
                            "[_locate_by_intent] v3 rag导航失败 → 回退当前页按钮"
                        )
            _log.info(
                "[_locate_by_intent] 语义匹配命中: operation=%r → button=%r (共%d个按钮)",
                operation[:30],
                _matched[:30],
                len(_btn_texts),
            )
            return _btn_locators[_matched]

        # 3. modal 内按钮（弹窗场景）
        _modal_loc = await _find_visible_modal(page)
        if _modal_loc is not None:
            _modal_btns = _modal_loc.locator("button:visible, [role='button']:visible")
            _modal_cnt = await _modal_btns.count()
            _modal_texts = []
            _modal_locators = {}
            for _i in range(min(_modal_cnt, 30)):
                try:
                    _loc = _modal_btns.nth(_i)
                    _txt = (await _loc.text_content() or "").strip()
                    if _txt and _txt not in _modal_texts:
                        _modal_texts.append(_txt)
                        _modal_locators[_txt] = _loc
                except Exception as _se27:
                    _log.debug(
                        "[locator] 异常降级(continue): %s",
                        str(_se27)[:60],
                        exc_info=True,
                    )
                    continue
            if _modal_texts:
                _matched_m = _match_button_by_keywords(operation, _modal_texts)
                if _matched_m and _matched_m in _modal_locators:
                    _log.info(
                        "[_locate_by_intent] modal内语义匹配命中: operation=%r → button=%r",
                        operation[:30],
                        _matched_m[:30],
                    )
                    return _modal_locators[_matched_m]

        _log.debug(
            "[_locate_by_intent] 当前页语义匹配未命中: operation=%r, buttons=%s",
            operation[:30],
            _btn_texts[:8],
        )

        # ★ 方案B增强 (ADR-017): rag_chunks 辅助参考 — 跨页面找按钮
        #   v3: 复用 _navigate_to_page_and_find_button（含域评分+Tab切换）
        _rag_result = _find_button_page_in_rag(operation)
        if _rag_result:
            _rag_url, _rag_btn = _rag_result
            _log.info(
                "[_locate_by_intent] rag_chunks辅助: operation=%r → page=%r button=%r",
                operation[:30],
                _rag_url[:60],
                _rag_btn[:30],
            )
            _domain_kws = _extract_domain_keywords(operation)
            _nav_loc = await _navigate_to_page_and_find_button(
                page, _rag_url, operation, _domain_kws
            )
            if _nav_loc is not None:
                return _nav_loc

        # ★ v4 通用修复 (ADR-017, 2026-07-23): Tier 4 — nav_map 兜底
        #   根因(§10.9): rag_chunks 只有8种按钮(顶部工具栏)，缺表格行内按钮(编辑/删除)
        #   修复: rag找不到时，用nav_map页面URL导航+实时扫描(含表格行内按钮)
        #   通用性: nav_map是页面URL清单，比rag_chunks更完整
        _domain_kws = _extract_domain_keywords(operation)
        _log.info(
            "[_locate_by_intent] v4 Tier4 nav_map兜底: op=%r kws=%s",
            operation[:30],
            _domain_kws[:5],
        )
        _navmap_loc = await _find_button_via_nav_map(page, operation, _domain_kws)
        if _navmap_loc is not None:
            return _navmap_loc

        # ★ v4 通用修复 (ADR-017, 2026-07-23): Tier 5 — 当前页 Tab 实时探索
        #   根因(§10.9): nav_map缺"辅材配方"Tab → nav_map兜底也找不到
        #   修复: 扫描当前页所有Tab元素 → 逐个点击 → 每次扫描按钮
        #   通用性: Tab元素是UI框架标准元素，实时探索不依赖预爬取
        _log.info("[_locate_by_intent] v4 Tier5 Tab实时探索: op=%r", operation[:30])
        _tab_loc = await _find_button_via_tab_exploration(page, operation, _domain_kws)
        if _tab_loc is not None:
            return _tab_loc

        return None
    except Exception as _e:
        _log.warning(
            "[_locate_by_intent] 异常: operation=%r err=%s",
            operation[:30],
            str(_e)[:100],
        )
        return None
