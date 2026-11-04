# -*- coding: utf-8 -*-
"""P2-10 元素工具 — 模块级通用函数（页面快照、元素匹配、点击、观察）。

通用性：只用原生HTML标签+W3C ARIA角色，零框架特定类名。
跨系统/行业/页面适用。
"""
from __future__ import annotations
from src.core.guarded import guard
from src.core.page_ready import (
    await_bounded,
    count_bounded,
    evaluate_bounded,
)

import logging
from typing import Any

_log = logging.getLogger(__name__)


# ── 页面快照获取 ──


async def get_page_snapshot(page) -> tuple[list[dict], list[dict]]:
    """获取页面实时交互元素（跨所有框架+原生HTML）。

    通用性保证：
    - 只用原生HTML标签(button/a/input/select/textarea) + W3C ARIA角色([role])
    - 零框架特定类名（无 .ant-* / .el-* / .btn）
    - 不过滤 offsetParent——包含折叠菜单/Portal/弹出层中的隐藏元素
    - 返回 visible 标记供 AI 决策，不在采集阶段过滤
    """
    _buttons = []
    _inputs = []
    with guard("get_page_snapshot: _buttons = await page.evaluate( '''(", log=_log, level=logging.WARNING):
        # ★ 通用性修复(2026-07-28): 移除 [role="tab"] 和 [class*="tab"] 选择器
        #   根因(5层why):
        #     1. TC-REQ-001~004 假PASS → Agent点击"烟叶数据"Tab而非"数据导入"按钮
        #     2. "烟叶数据"Tab被点击 → 页面变化 → 判定PASS → 假阳性
        #     3. Tab在按钮列表中 → get_page_snapshot 收集了 [role="tab"] 元素
        #     4. Tab未被过滤 → _execute_trigger_click 只过滤 menuitem/treeitem，不过滤 tab
        #     5. Tab是导航元素，不是操作按钮 → 点击Tab≠执行业务操作
        #   通用性: 跨系统/行业/页面 — 所有Web应用都有Tab vs 按钮的区别
        # ★ 2026-10-27: 求值经 evaluate_bounded 有界（扫描期无用例级 wait_for 包裹，
        #   执行上下文不可用时 page.evaluate 会永不返回）。超时返回 [] 而非抛错。
        _buttons = await evaluate_bounded(
            page,
            """() => {
            const els = document.querySelectorAll(
                'button, a, [role="button"], [role="menuitem"], [role="link"], '
                + '[role="treeitem"], [onclick], [tabindex]:not([tabindex="-1"]), '
                + 'span[onclick], div[onclick], li[onclick], '
                + '[class*="btn"], [class*="button"], '
                + 'input[type="button"], input[type="submit"]'
            );
            return Array.from(els).map(el => ({
                tag: el.tagName.toLowerCase(),
                text: (el.textContent || '').trim().substring(0, 100),
                role: el.getAttribute('role') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                ariaExpanded: el.getAttribute('aria-expanded') || '',
                ariaHasPopup: el.getAttribute('aria-haspopup') || '',
                ariaSelected: el.getAttribute('aria-selected') || '',
                title: el.getAttribute('title') || '',
                href: el.getAttribute('href') || '',
                region: el.closest(
                    'nav, header, footer, aside, '
                    + '[role="navigation"], [role="banner"], '
                    + '[role="contentinfo"], [role="complementary"]'
                ) ? 'nav' : 'main',
                visible: el.offsetParent !== null,
                display: window.getComputedStyle(el).display || '',
            }));
        }"""
        ) or []

    with guard("get_page_snapshot: _inputs = await page.evaluate( '''()", log=_log, level=logging.WARNING):
        _inputs = await evaluate_bounded(
            page,
            """() => {
            const els = document.querySelectorAll(
                'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), '
                + 'textarea, [role="textbox"], [role="searchbox"], [role="combobox"], select'
            );
            return Array.from(els).map(el => ({
                tag: el.tagName.toLowerCase(),
                placeholder: el.getAttribute('placeholder') || '',
                name: el.getAttribute('name') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                text: (el.getAttribute('placeholder') || el.getAttribute('name') || '').trim(),
                region: el.closest(
                    'nav, header, footer, aside, '
                    + '[role="navigation"], [role="banner"], '
                    + '[role="contentinfo"], [role="complementary"]'
                ) ? 'nav' : 'main',
                visible: el.offsetParent !== null,
                display: window.getComputedStyle(el).display || '',
            }));
        }"""
        ) or []

    return _buttons, _inputs


async def page_reconnaissance(
    page, module_hint: str = ""
) -> tuple[list[dict], list[dict]]:
    """★ v5.14(2026-07-31): 页面侦察——系统化展开+滚动+重取快照，确保无遗漏。

    根因(§10.9 5层why):
      L1: 按钮找不到 → get_page_snapshot未捕获
      L2: 元素未渲染 → 在折叠面板/懒加载区域/视口外
      L3: 页面未完全展开 → 仅展开侧边栏，未展开页面内容区的可折叠区域
      L4: 缺少页面侦察步骤 → 直接搜索按钮，不先确保页面内容已完全暴露
      L5: 根本原因——Agent缺少"先侦察再搜索"的战术模式

    通用性: 跨系统/行业/框架 — 所有Web应用都有可折叠面板/懒加载/滚动区域。
    策略: ① 滚动全页 → ② 展开所有可折叠区域 → ③ 重新获取快照。
    """
    import asyncio

    try:
        # ① 滚动页面确保懒加载内容可见
        #   ★ 2026-10-27: 所有求值经 evaluate_bounded 有界（侦察期无用例级 wait_for 包裹）。
        #   首探即失败（执行上下文不可用）→ 放弃滚动/展开，直接按有界快照返回，
        #   避免后续十余次求值逐个等满上界。
        _page_height = await evaluate_bounded(page, "document.body.scrollHeight")
        if _page_height is None:
            _log.warning("[Recon] 页面求值不可用，跳过滚动/展开，直接取快照")
            return await get_page_snapshot(page)
        await evaluate_bounded(page, "() => { window.scrollTo(0, 0); return true; }")
        await asyncio.sleep(0.3)
        _step = 400
        for _y in range(0, min(int(_page_height), 5000), _step):
            _scrolled = await evaluate_bounded(
                page, f"() => {{ window.scrollTo(0, {_y}); return true; }}"
            )
            if _scrolled is None:
                break
            await asyncio.sleep(0.1)
        # 滚回顶部
        await evaluate_bounded(page, "() => { window.scrollTo(0, 0); return true; }")
        await asyncio.sleep(0.3)

        # ② 展开所有可折叠区域
        # 通用ARIA展开: collapse/expandable区域
        _expand_selectors = [
            "[aria-expanded='false']",
            "[class*='collapse']:not([class*='expanded'])",
            "[class*='panel']:not([class*='open'])",
            "details:not([open])",
            "[class*='accordion'] [class*='header']",
            "[class*='card'] [class*='header']:not([class*='open'])",
        ]
        _expanded = 0
        for _sel in _expand_selectors:
            _els = page.locator(_sel)
            _cnt = await count_bounded(_els)
            if _cnt <= 0:
                continue
            for _i in range(min(_cnt, 20)):
                try:
                    if await await_bounded(
                        _els.nth(_i).is_visible(), default=False, what="is_visible"
                    ):
                        await _els.nth(_i).click(timeout=2000)
                        _expanded += 1
                        await asyncio.sleep(0.15)
                except Exception:
                    continue
        if _expanded > 0:
            _log.info("[Recon] 展开了%d个可折叠区域", _expanded)
            await asyncio.sleep(0.5)

        # ③ 重新获取快照
        return await get_page_snapshot(page)
    except Exception as _e:
        _log.warning("[Recon] 页面侦察异常: %s", str(_e)[:80])
        return await get_page_snapshot(page)


async def get_page_snapshot_text(page) -> str:
    """获取页面文本快照（供LLM语义断言使用）。"""
    _txt = await evaluate_bounded(
        page,
        """() => {
            return (document.body ? document.body.innerText : '').substring(0, 2000);
        }""",
    )
    return str(_txt or "")


async def get_page_tabs(page) -> list[dict]:
    """获取页面Tab元素（独立于get_page_snapshot，用于Tab探索）。

    ★ 通用性修复(2026-07-28): 独立函数，与get_page_snapshot解耦。
    根因: get_page_snapshot 移除了[role=tab]选择器（防假PASS），
    但 _explore_tabs_for_button 依赖 dom_buttons 获取Tab → 被静默破坏。
    通用性: 跨系统/行业/页面 — 所有Web应用都有Tab导航。
    """
    try:
        # ★ Fix(2026-07-29): 移除[aria-selected]和[class*="tab"]宽泛选择器
        # 根因(5层why):
        #   1. Agent探索超时 → 探索13个"假Tab"浪费时间
        #   2. 13个假Tab → [aria-selected]匹配了所有带aria-selected的元素
        #   3. [aria-selected]太宽泛 → 表格行、列表项等都可能带aria-selected
        #   4. [class*="tab"]也宽泛 → 匹配class含"tab"的所有元素
        #   5. 宽泛选择器 → 假Tab数量爆炸，Agent浪费探索步数
        # 通用性: 跨系统/行业/页面 — 只匹配role=tab是W3C标准做法
        _tabs = await evaluate_bounded(
            page,
            """() => {
            const els = document.querySelectorAll(
                '[role="tab"]'
            );
            return Array.from(els).map(el => ({
                tag: el.tagName.toLowerCase(),
                text: (el.textContent || '').trim().substring(0, 100),
                role: el.getAttribute('role') || 'tab',
                ariaSelected: el.getAttribute('aria-selected') || '',
                visible: el.offsetParent !== null,
                display: window.getComputedStyle(el).display || '',
            }));
        }""",
        )
        return _tabs or []
    except Exception as _e:
        _log.warning("[TabSnapshot] Tab元素获取失败: %s", str(_e)[:80])
        return []


async def get_inpage_nav_links(page) -> list[dict]:
    """获取页内导航链接（非侧边栏、非Tab的页内子页面导航）。

    ★ 通用性修复(2026-07-29): 新增函数。
    根因(5层why):
      1. TC-REQ-001-I01超时 → Agent找不到"数据导入"按钮
      2. 按钮在"物料管理"子页面 → 需要点击页内导航链接才能到达
      3. Agent不知道页内导航 → 只探索Tab和侧边栏，不探索页内链接
      4. 页内导航未被捕获 → get_page_snapshot不包含这些元素
      5. 缺少页内导航探索策略 → Agent永远找不到子页面中的按钮
    通用性: 跨系统/行业/页面 — 所有Web应用都可能有页内二级导航
    （如Ant Design Pro的PageContainer tabList、自定义TabBar等）
    """
    try:
        _nav_links = await evaluate_bounded(
            page,
            """() => {
            const result = [];
            // 策略: 查找主内容区域中短文本的可点击元素
            // 排除: 侧边栏(ul[class*="menu"]/aside)、表格、按钮、输入框
            const mainArea = document.querySelector('main, [role="main"], .content, #content');
            const searchRoot = mainArea || document.body;

            // 收集所有可能为导航链接的元素
            const candidates = searchRoot.querySelectorAll(
                'a, span, div, li'
            );

            const seen = new Set();
            for (const el of candidates) {
                const text = (el.textContent || '').trim();
                // 导航链接特征: 短文本(2-15字)、不在侧边栏内
                if (!text || text.length < 2 || text.length > 15) continue;
                if (seen.has(text)) continue;
                seen.add(text);

                // 排除: 按钮、输入框、侧边栏内元素
                const tag = el.tagName.toLowerCase();
                if (tag === 'button' || tag === 'input' || tag === 'textarea') continue;
                if (el.closest('aside, [class*="sidebar"], [class*="sider"], ul[class*="menu"]')) continue;
                // 排除: 表格单元格
                if (el.closest('table, [role="grid"]')) continue;
                // 排除: Tab (已由get_page_tabs单独处理)
                if (el.getAttribute('role') === 'tab') continue;
                // 排除: 纯文本节点(无交互性) — ★ v5.17: 移到仪表盘网格检查之前
                //   根因: v5.12的仪表盘网格过滤器在hasInteraction检查之前，导致
                //         "产品管理""配方管理"等可点击的仪表盘卡片被错误排除。
                //         这些卡片是通向数据表格子页面的入口，Agent无法找到按钮。
                //   修复: 先检查hasInteraction，可点击元素不排除(即使是仪表盘卡片)。
                //   通用性: 跨系统/行业—所有Dashboard都有可点击的导航卡片。
                const hasInteraction = (
                    el.tagName === 'A' ||
                    el.hasAttribute('onclick') ||
                    el.getAttribute('role') === 'link' ||
                    el.getAttribute('role') === 'button' ||
                    el.tabIndex >= 0 ||
                    el.closest('[onclick]') ||
                    // 检查是否有cursor:pointer样式(通常表示可点击)
                    window.getComputedStyle(el).cursor === 'pointer'
                );
                if (!hasInteraction) continue;

                result.push({
                    tag: tag,
                    text: text,
                    visible: el.offsetParent !== null,
                });
            }
            return result.slice(0, 20);  // 限制数量
        }""",
        )
        return _nav_links or []
    except Exception as _e:
        _log.warning("[InPageNav] 页内导航获取失败: %s", str(_e)[:80])
        return []


# ── 元素匹配 ──


def el_to_selector(el: dict) -> str:
    """元素快照 dict → CSS 选择器（快照不带 selector 字段，回退 name/placeholder）。

    快照元素（get_page_snapshot）本身无 selector；name/placeholder 是表单控件的
    稳定属性，select/textarea/input 同规则构造。全引擎唯一入口，下游定位只读不另推。
    """
    _sel = (el.get("selector") or "").strip()
    if _sel:
        return _sel
    _tag = (el.get("tag") or "").strip().lower()
    _name = el.get("name") or ""
    _ph = el.get("placeholder") or ""
    if _name:
        return '%s[name="%s"]' % (_tag, _name) if _tag else '[name="%s"]' % _name
    if _ph:
        return (
            '%s[placeholder="%s"]' % (_tag, _ph)
            if _tag
            else '[placeholder="%s"]' % _ph
        )
    return ""


def match_keywords_to_elements(
    keywords: list[str],
    elements: list[dict],
) -> dict[str, Any] | None:
    """关键词匹配元素: 主关键词权重5, 2-gram权重3, 多关键词门禁(score>=5)。

    ★ 通用性修复(2026-07-29): 同分时优先短文本+<button>元素
    根因(5层why):
      1. TC-REQ-001-I01超时 → Agent点击了错误的元素(tabpanel div)
      2. 点击了tabpanel div → match_keywords_to_elements返回了div而非button
      3. div被匹配 → tabpanel div包含"数据导入"文本，score=5
      4. button也被匹配 → button text="数据导入"也score=5
      5. 同分无优先级 → div在DOM中先出现，被优先返回
    通用性: 跨系统/行业/页面 — 所有Web应用都有大容器div包含按钮文本
    """
    _best_match = None
    _best_score = 0
    _best_hit_count = 0
    _best_text_len = 999999  # ★ 越小越好（短文本更精确）
    _best_is_button = False  # ★ button元素优先
    for _el in elements:
        _el_text = (_el.get("text") or "").strip()
        _el_aria = (_el.get("ariaLabel") or "").strip()
        _el_title = (_el.get("title") or "").strip()
        _el_placeholder = (_el.get("placeholder") or "").strip()

        _score = 0
        _hit_kw = set()
        _primary_kw = keywords[0] if keywords else ""
        for _kw in keywords:
            _is_primary = _kw == _primary_kw
            if _kw in _el_text:
                _score += 5 if _is_primary else 3
                _hit_kw.add(_kw)
            elif _kw in _el_aria:
                _score += 3 if _is_primary else 2
                _hit_kw.add(_kw)
            elif _kw in _el_title:
                _score += 2 if _is_primary else 1
                _hit_kw.add(_kw)
            elif _kw in _el_placeholder:
                _score += 2 if _is_primary else 1
                _hit_kw.add(_kw)
        _hit_count = len(_hit_kw)
        if _hit_count < 1 or _score < 5:
            _score = 0
        _text_len = len(_el_text)
        _is_button = _el.get("tag") == "button"
        # ★ 选择逻辑: 同分时优先短文本，再优先button元素
        _better = False
        if _score > _best_score:
            _better = True
        elif _score == _best_score and _score > 0:
            # 同分: 优先短文本
            if _text_len < _best_text_len:
                _better = True
            elif _text_len == _best_text_len:
                # 同分+同长度: 优先button
                if _is_button and not _best_is_button:
                    _better = True
        if _better:
            _best_score = _score
            _best_match = _el
            _best_hit_count = _hit_count
            _best_text_len = _text_len
            _best_is_button = _is_button

    if _best_match is None:
        return None
    return {"element": _best_match, "score": _best_score, "hit_count": _best_hit_count}


# ── 点击操作 ──


async def click_button(page, btn_text: str, timeout_ms: int) -> bool:
    """Playwright定位点击，增强健壮性。

    策略（逐级fallback）:
    1. get_by_text（最可靠，支持中文/嵌套文本）
    2. CSS选择器（兜底，处理特殊场景，仅用原生+ARIA标签）
    3. force=True（处理被遮挡/不可见元素，如折叠菜单中的项）
    4. 多次尝试（处理动画/懒加载）
    通用性: 跨 React/Vue/Angular/原生HTML，无框架特定类名。
    """
    import asyncio

    _timeout = min(timeout_ms, 30000)
    _click_timeout = min(_timeout, 8000)

    # 策略1: get_by_text（最通用，Playwright原生文本匹配）
    _loc = page.get_by_text(btn_text, exact=False).first
    with guard("click_button: if await _loc.count() > 0: await _lo", log=_log):
        if await count_bounded(_loc) > 0:
            await _loc.click(timeout=_click_timeout)
            return True

    # 策略2: CSS选择器逐级尝试（含通用ARIA标签）
    _selectors = [
        f"button:has-text('{btn_text}')",
        f"a:has-text('{btn_text}')",
        f"[role='button']:has-text('{btn_text}')",
        f"[role='menuitem']:has-text('{btn_text}')",
        f"li:has-text('{btn_text}')",
        f"[role='tab']:has-text('{btn_text}')",
        f"[role='treeitem']:has-text('{btn_text}')",
        f"[role='link']:has-text('{btn_text}')",
    ]
    for _sel in _selectors:
        try:
            _el = page.locator(_sel).first
            if await count_bounded(_el) > 0:
                await _el.click(timeout=_click_timeout)
                return True
        except Exception:
            continue

    # 策略3: force=True（处理被遮挡/不可见元素，如折叠菜单中的项）
    with guard("click_button: _el = page.get_by_text(btn_text, exa", log=_log):
        _el = page.get_by_text(btn_text, exact=False).first
        if await count_bounded(_el) > 0:
            await _el.click(force=True, timeout=_click_timeout)
            return True

    # 策略4: 多次重试（处理动画/懒加载）
    for _attempt in range(2):
        try:
            await asyncio.sleep(0.5)
            _el = page.get_by_text(btn_text, exact=False).first
            if await count_bounded(_el) > 0:
                await _el.click(force=True, timeout=_click_timeout)
                return True
        except Exception:
            continue

    _log.debug("[Click] 所有策略失败: %r", btn_text[:30])
    return False


async def try_pw_locate_button(
    page, primary_kw: str, short_kw: str
) -> dict[str, Any] | None:
    """Playwright locator 直接搜索按钮（处理 portal/overlay/下拉菜单/自定义组件）。

    通用性增强: 优先匹配<button>标签，避免get_by_text匹配到span/div等非按钮元素。
    ★ 通用性修复(2026-07-29): button:has-text优先于get_by_text
    根因(5层why):
      1. TC-REQ-001-I01超时 → Agent找不到"数据导入"按钮
      2. 按钮在"物料管理"Tab内 → _explore_inpage_nav_for_button点击Tab后查找
      3. try_pw_locate_button返回span → get_by_text("数据导入")匹配到button内的<span>
      4. span匹配优先于button → get_by_text遍历DOM，span在button内部先被匹配
      5. 缺少button优先级 → 应先尝试button:has-text再fallback到get_by_text
    通用性: 跨系统/行业/页面 — 所有Web应用都有button内嵌span的结构
    """
    with guard("try_pw_locate_button: for _kw in (primary_kw, short_kw): i", log=_log):
        for _kw in (primary_kw, short_kw):
            if not _kw or len(_kw) < 2:
                continue
            # ★ 策略1: 优先匹配<button>标签（通用性：button:has-text最精确）
            _btn_loc = page.locator(f"button:has-text('{_kw}')").first
            with guard("try_pw_locate_button: if await _btn_loc.count() > 0 and aw", log=_log):
                if await count_bounded(_btn_loc) > 0 and await await_bounded(
                    _btn_loc.is_visible(), default=False, what="is_visible"
                ):
                    _text = str(
                        await await_bounded(
                            _btn_loc.text_content(), default="", what="text_content"
                        )
                        or ""
                    ).strip()[:100]
                    _tag = (
                        await evaluate_bounded(_btn_loc, "el => el.tagName.toLowerCase()")
                        or ""
                    )
                    _role = (
                        await await_bounded(
                            _btn_loc.get_attribute("role"),
                            default="",
                            what="get_attribute",
                        )
                        or ""
                    )
                    _log.info(
                        "[PWLocate] button:has-text命中: tag=%s text=%r",
                        _tag,
                        _text[:60],
                    )
                    return {
                        "element": {"text": _text, "tag": _tag, "role": _role},
                        "score": 10,
                        "hit_count": 1,
                    }
            # ★ 策略2: get_by_text（兜底，但检查tag是否为button）
            _loc = page.get_by_text(_kw, exact=False).first
            if await count_bounded(_loc) > 0:
                with guard("try_pw_locate_button: if await _loc.is_visible(): _text = ", log=_log):
                    if await await_bounded(
                        _loc.is_visible(), default=False, what="is_visible"
                    ):
                        _text = str(
                            await await_bounded(
                                _loc.text_content(), default="", what="text_content"
                            )
                            or ""
                        ).strip()[:100]
                        _tag = (
                            await evaluate_bounded(_loc, "el => el.tagName.toLowerCase()")
                            or ""
                        )
                        _role = (
                            await await_bounded(
                                _loc.get_attribute("role"),
                                default="",
                                what="get_attribute",
                            )
                            or ""
                        )
                        # ★ 如果get_by_text返回的不是button，继续尝试CSS选择器
                        if _tag not in ("button", "a"):
                            _log.debug(
                                "[PWLocate] get_by_text返回非button元素(tag=%s), 继续CSS选择器",
                                _tag,
                            )
                        else:
                            return {
                                "element": {"text": _text, "tag": _tag, "role": _role},
                                "score": 10,
                                "hit_count": 1,
                            }
            # ★ 策略3: CSS选择器（兜底，覆盖自定义组件）
            for _sel_tpl in [
                "a:has-text('{kw}')",
                "[role='button']:has-text('{kw}')",
                "[role='menuitem']:has-text('{kw}')",
                "[role='link']:has-text('{kw}')",
                "[role='tab']:has-text('{kw}')",
                "li:has-text('{kw}')",
                "span:has-text('{kw}')",
                "div[onclick]:has-text('{kw}')",
                "[tabindex]:has-text('{kw}')",
            ]:
                try:
                    _loc = page.locator(_sel_tpl.format(kw=_kw)).first
                    if await count_bounded(_loc) > 0 and await await_bounded(
                        _loc.is_visible(), default=False, what="is_visible"
                    ):
                        _text = str(
                            await await_bounded(
                                _loc.text_content(), default="", what="text_content"
                            )
                            or ""
                        ).strip()[:100]
                        _tag = (
                            await evaluate_bounded(_loc, "el => el.tagName.toLowerCase()")
                            or ""
                        )
                        _role = (
                            await await_bounded(
                                _loc.get_attribute("role"),
                                default="",
                                what="get_attribute",
                            )
                            or ""
                        )
                        return {
                            "element": {"text": _text, "tag": _tag, "role": _role},
                            "score": 10,
                            "hit_count": 1,
                        }
                except Exception:
                    continue
    return None


async def repair_click(page, btn_text: str, timeout_ms: int) -> bool:
    """修复点击（模糊匹配）。"""
    with guard("repair_click: _locator = page.get_by_text(btn_text", log=_log):
        _locator = page.get_by_text(btn_text, exact=False).first
        if await count_bounded(_locator) > 0:
            await _locator.first.click(timeout=timeout_ms)
            await page.wait_for_timeout(1500)
            return True
    return False


# ── 结果观察 ──


async def observe_page_change(page, operation: str) -> str:
    """观察页面变化（URL变化/表单打开/提示消息）。"""
    try:
        _dialog_visible = await check_dialog_visible(page)
        _message = await get_page_message(page)

        if _dialog_visible:
            return "弹窗/对话框已打开"
        if _message:
            return f"系统提示: {_message}"
        return "无可见变化"
    except Exception as _e:
        return f"观察异常: {str(_e)[:50]}"


async def check_dialog_visible(page) -> bool:
    """检查弹窗/对话框是否可见。

    通用性: 优先W3C ARIA角色，框架特定类名作为兜底。
    ★ v5.5 fix(2026-07-30): 移除 .ant-form/.el-form/form[class*="form"] —
    表单元素不是弹窗，会导致误判（如portraitData页面的筛选表单）。
    """
    _visible = await evaluate_bounded(
        page,
        """() => {
            return !!document.querySelector(
                '[role="dialog"], [role="alertdialog"], '
                + '.ant-modal:not([style*="display: none"]), '
                + '.el-dialog:not([style*="display: none"]), '
                + '.ant-drawer:not([style*="display: none"])'
            );
        }""",
    )
    return bool(_visible)


async def get_page_message(page) -> str:
    """获取页面提示消息。

    通用性: 优先W3C ARIA角色，框架特定类名作为兜底。
    """
    _msg = await evaluate_bounded(
        page,
        """() => {
            const m = document.querySelector(
                '.ant-message, .el-message, .ant-notification, '
                + '[role="alert"], .toast, .message-tip, .ant-alert'
            );
            return m ? (m.textContent || '').trim().substring(0, 200) : '';
        }""",
    )
    return str(_msg or "")


# ── 操作类型分类 ──

_OP_CLASSIFIERS: list[tuple[str, list[str]]] = [
    ("search", ["搜索", "查询", "筛选", "查找", "检索"]),
    ("create", ["新建", "新增", "添加", "创建", "增加"]),
    ("edit", ["编辑", "修改", "更新", "变更", "调整"]),
    ("delete", ["删除", "移除", "清空", "销毁"]),
    ("import_export", ["导入", "上传", "导出", "下载", "批量导入", "批量导出"]),
    ("view", ["查看", "详情", "预览", "浏览"]),
]


def clean_operation(operation: str) -> str:
    """清洗operation提取核心动作: 【数据导入】（P3辅材）→数据导入"""
    import re

    _op = (operation or "").strip()
    if not _op:
        return _op
    _bracket_match = re.search(r"【([^】]+)】", _op)
    if _bracket_match:
        _core = _bracket_match.group(1).strip()
        if _core:
            return _core
    _module_idx = _op.find("所属模块")
    if _module_idx > 0:
        _op = _op[:_module_idx].strip()
    _op = re.sub(r"（[^）]*）", "", _op).strip()
    _op = re.sub(r"【[^】]*】", "", _op).strip()
    return _op if _op else operation


def classify_operation(operation: str) -> str:
    """根据操作描述自动分类操作类型（通用性：基于中文操作词）。"""
    _op = operation or ""
    for _type, _keywords in _OP_CLASSIFIERS:
        for _kw in _keywords:
            if _kw in _op:
                return _type
    return "generic"


def extract_keywords(operation: str) -> list[str]:
    """提取中文关键词（v3 短语提取，替代2-gram）。

    根因（5层why）：
    1. TC-REQ-001~004 假PASS → 匹配到"烟叶数据"Tab而非真实按钮
    2. "烟叶数据"Tab得分6 → 2-gram 生成了"数据""烟叶"等噪声关键词
    3. 2-gram 噪声 → 没有过滤通用词（"数据"出现在很多元素中）
    4. 真实按钮不在快照中 → 按钮在页面加载时不可见
    5. Agent匹配到Tab后点击→弹窗打开→判定PASS → 假阳性

    v3 修复：短语提取替代2-gram，过滤通用词。
    通用性：跨系统/行业/语言 — 所有中文操作文本适用。
    """
    import logging as _logging
    import re

    _logging.getLogger("src.agent.element_utils.extract_keywords").info(
        "[KW-DEBUG] extract_keywords v3(phrase) input=%r", operation[:80]
    )

    _keywords = []

    # 1. 提取【...】内容（主操作名，如"数据导入""批量打标"）
    _bracket_matches = re.findall(r"【(.+?)】", operation)
    for _m in _bracket_matches:
        _m = _m.strip()
        if len(_m) >= 2:
            _keywords.append(_m)

    # 2. 提取 所属模块：... 内容（模块名，如"烟叶画像数据"）
    _module_match = re.search(r"所属模块[：:]\s*(.+?)(?:$|[\s，,])", operation)
    if _module_match:
        _mod = _module_match.group(1).strip()
        if len(_mod) >= 2:
            _keywords.append(_mod)

    # 3. 无【】时，提取3+字的有意义短语
    if not _bracket_matches:
        _parts = re.split(r"[【】（）()\s，,。．、：:]+", operation)
        for _part in _parts:
            _part = _part.strip()
            if len(_part) >= 3:
                _keywords.append(_part)

    # 4. 生成子关键词（2-3字片段），解决长关键词无法匹配短按钮的根本问题
    #    根因: "编辑感官数据"无法匹配表格行中的"编辑"按钮
    #    通用性: 跨系统/行业/页面 — 所有Web应用都有复合操作名+短按钮文本
    #    注意: 只对步骤1-3提取的有意义短语生成子关键词，不拆分完整operation文本
    _sub_keywords = []
    for _k in list(_keywords):
        # 对≥4字的中文关键词，生成2-3字滑动窗口子片段
        if len(_k) >= 4:
            # 2字片段
            for _i in range(len(_k) - 1):
                _sub = _k[_i : _i + 2]
                if len(_sub) >= 2:
                    _sub_keywords.append(_sub)
            # 3字片段
            if len(_k) >= 5:
                for _i in range(len(_k) - 2):
                    _sub = _k[_i : _i + 3]
                    if len(_sub) >= 3:
                        _sub_keywords.append(_sub)
    _keywords.extend(_sub_keywords)

    # 5. 完整operation作为兜底（放在子关键词生成之后，避免长文本产生大量垃圾子词）
    _op_clean = operation.strip()
    if _op_clean and _op_clean not in _keywords:
        _keywords.append(_op_clean)

    # 6. 过滤通用词（2字高频词，容易匹配到无关元素）
    #    ★ 修复: 移除"编辑""删除""新增""导入""导出"等操作词
    #    根因: 这些词是精准的操作按钮文本，不是通用词，过滤后导致无法匹配
    _COMMON_WORDS = {
        "数据",
        "管理",
        "信息",
        "系统",
        "配置",
        "设置",
        "查询",
        "统计",
        "分析",
        "处理",
        "显示",
        "展示",
        "操作",
        "功能",
        "服务",
        "模块",
        "页面",
        "列表",
        "详情",
        "用户",
        "权限",
        "角色",
        "部门",
        "组织",
        "公司",
        "单位",
        "所属",
        "优化",
        "版本",
        "二期",
        "P2",
        "P3",
        "P0",
    }
    _filtered = [k for k in _keywords if k not in _COMMON_WORDS]
    if _filtered:
        _keywords = _filtered

    # 去重保序
    _seen = set()
    _result = []
    for _k in _keywords:
        if _k not in _seen:
            _seen.add(_k)
            _result.append(_k)

    return _result if _result else [operation]


def compute_intent_hash(operation: str, target_page: str) -> str:
    """计算意图哈希（与 op_cache_v2 一致）。"""
    import hashlib

    _raw = f"{operation}|{target_page}"
    return hashlib.sha256(_raw.encode("utf-8")).hexdigest()
