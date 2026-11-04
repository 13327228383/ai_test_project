# -*- coding: utf-8 -*-
"""tab_navigation.py — Tab 页面切换通用逻辑（从 browser_structured_executor.py 拆分，§4.3 单文件≤800行）。

职责：navigate 后确保 URL tab 参数对应的 Tab 元素被激活。
覆盖：所有框架的 Tab 组件（通过 W3C ARIA [role=tab] + [aria-selected] 通用检测）。

通用性(§12.4 · P0-2026-07-28):
  - 跨系统: 只用 [role=tab] + [aria-selected]（W3C标准），不依赖框架特定类名
  - 跨Tab: 所有 tab 参数都能处理（不依赖特定 Tab 名）
  - 数据完整性: 实时检测页面Tab元素，不依赖静态 page_dom_snapshots 表
  - 合理性: Tab 切换是 navigate 的"语义实现"（决策在 Step3，执行在 Step4）
  - 覆盖性: 覆盖所有含 tab 参数的 navigate action
"""

import asyncio
from src.core.guarded import guard
import logging
from urllib.parse import urlparse, parse_qs

from src.pipeline.steps.step4.playwright_engine.selectors import _escape_css_str

_log = logging.getLogger(__name__)


async def _ensure_tab_active_after_navigate(engine, nav_url: str) -> bool:
    """navigate 后置处理：确保 URL 中 tab 参数对应的 Tab 元素被激活。

    通用修复(2026-07-20 v10.1): Tab 页面 URL 参数不触发 Tab 切换的通用兜底。
    v10.1 修正: 检测页面上 aria-selected="true" 的 Tab 元素（不依赖 URL 参数匹配）。
    v2 修正: URL tab 参数已等于目标值时直接返回 True，避免盲目点击把正确 Tab 切走。
    """
    if not nav_url:
        return True
    try:
        _parsed = urlparse(nav_url)
        _qs = parse_qs(_parsed.query)
        _target_tab_value = (_qs.get("tab", [""])[0] or "").strip()
        if not _target_tab_value:
            return True  # 无 tab 参数，无需处理

        page = engine._page
        if page is None:
            return True

        # 等待页面渲染（Ant Design Tabs 渲染需要时间）
        await asyncio.sleep(1.0)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception as _e:
            _log.debug(
                "[tab_navigation] domcontentloaded 等待异常: %s", _e, exc_info=True
            )

        # 步骤1: 实时检测页面Tab元素，找到目标tab_value对应的Tab文本
        _target_tab_text = await _find_tab_text_by_value(page, _target_tab_value)
        if not _target_tab_text:
            return await _handle_snapshot_miss(page, _target_tab_value)

        # 步骤2: 检测页面上 aria-selected="true" 的 Tab 元素文本是否匹配
        _current_active_tab = await _detect_current_active_tab_text(page)
        _log.info(
            "[TabSwitch] 目标Tab='%s' 当前activeTab='%s'",
            _target_tab_text,
            _current_active_tab or "(未检测到)",
        )

        if _current_active_tab == _target_tab_text:
            _log.info(
                "[TabSwitch] ✓ 当前页面 active Tab 已是目标 Tab '%s'，无需切换",
                _target_tab_text,
            )
            return True

        # 步骤3: 点击目标 Tab 元素
        _clicked = await _click_tab_element_by_text(page, _target_tab_text)
        if _clicked:
            _log.info("[TabSwitch] ✓ 点击 Tab '%s' 成功", _target_tab_text)
            await asyncio.sleep(1.0)
            _new_active_tab = await _detect_current_active_tab_text(page)
            if _new_active_tab == _target_tab_text:
                _log.info(
                    "[TabSwitch] ✓ 校验通过: active Tab 已切换为 '%s'", _target_tab_text
                )
                return True
            _log.warning(
                "[TabSwitch] 点击后 active Tab='%s' 与目标='%s' 不一致",
                _new_active_tab,
                _target_tab_text,
            )
            return True  # 已点击，不阻塞

        # 步骤4: 兜底 - 遍历页面所有 Tab 元素
        _log.warning(
            "[TabSwitch] 目标 Tab '%s' 点击失败，尝试遍历所有 Tab 元素",
            _target_tab_text,
        )
        _fallback_clicked = await _click_tab_by_try_all(page, _target_tab_value)
        if _fallback_clicked:
            _log.info("[TabSwitch] ✓ 遍历 Tab 元素成功切换到 tab=%s", _target_tab_value)
            await asyncio.sleep(0.8)
            return True

        _log.warning("[TabSwitch] ✗ Tab 切换失败，目标 tab=%s", _target_tab_value)
        return False
    except Exception as _e:
        _log.warning("[TabSwitch] 异常(不阻塞主流程): %s", str(_e)[:120], exc_info=True)
        return True  # 异常不阻塞主流程


async def _handle_snapshot_miss(page, target_tab_value: str) -> bool:
    """实时检测无结果时的兜底处理：URL参数校验 + 遍历点击。

    P0-通用性(2026-07-28): 不依赖静态 page_dom_snapshots 表，实时探索页面Tab元素。
    """
    _log.info(
        "[TabSwitch] 实时检测未找到 tab=%s 的 Tab 文本，兜底遍历点击", target_tab_value
    )
    _cur_url = page.url or ""
    _cur_tab = (parse_qs(urlparse(_cur_url).query).get("tab", [""])[0] or "").strip()
    if _cur_tab == target_tab_value:
        _log.info(
            "[TabSwitch] ✓ URL tab 参数已是目标值 '%s'，Tab 切换由 navigate 完成",
            target_tab_value,
        )
        return True
    _fallback_clicked = await _click_tab_by_try_all(page, target_tab_value)
    if _fallback_clicked:
        _log.info("[TabSwitch] ✓ 遍历 Tab 元素成功切换到 tab=%s", target_tab_value)
        await asyncio.sleep(1.0)
        return True
    _log.warning("[TabSwitch] ✗ 遍历 Tab 元素也未能切换到 tab=%s", target_tab_value)
    return False


async def _find_tab_text_by_value(page, tab_value: str) -> str:
    """★ P0-通用性(2026-07-28): 实时检测页面Tab元素，找到tab_value对应的Tab文本。

    替代旧的 _lookup_active_tab_text_from_snapshots（查询静态 page_dom_snapshots 表）。
    通用性: 只用 [role=tab]（W3C标准），跨所有框架。
    策略: 遍历页面所有 [role=tab] 元素，匹配文本/aria-label/value → 返回匹配的文本。
    """
    try:
        _tabs = page.locator('[role="tab"]')
        _cnt = await _tabs.count()
        for _i in range(min(_cnt, 20)):
            try:
                _item = _tabs.nth(_i)
                _text = (await _item.text_content() or "").strip()
                _aria_label = (await _item.get_attribute("aria-label") or "").strip()
                _value = (await _item.get_attribute("value") or "").strip()
                # 匹配: tab_value 出现在 text/aria-label/value 中
                if (
                    tab_value in _text
                    or tab_value in _aria_label
                    or tab_value == _value
                ):
                    if _text:
                        _log.info(
                            "[TabSwitch] 实时匹配: tab_value=%r → tab_text=%r",
                            tab_value,
                            _text[:40],
                        )
                        return _text
            except Exception:
                continue
        return ""
    except Exception as _e:
        _log.warning("[TabSwitch] 实时检测Tab元素异常: %s", str(_e)[:80])
        return ""


async def _detect_current_active_tab_text(page) -> str:
    """检测页面上当前激活的 Tab 元素文本。

    P0-通用性(2026-07-28): 只用 [role=tab][aria-selected="true"]（W3C标准）。
    移除框架特定类名(.ant-tabs-tab-active, .el-tabs__item.is-active, .nav-tabs li.active)。
    """
    with guard("_detect_current_active_tab_text: _loc = page.locator('[role='tab'][ar", log=_log):
        _loc = page.locator('[role="tab"][aria-selected="true"]').first
        if await _loc.count() > 0:
            _text = (await _loc.text_content() or "").strip()
            if _text:
                return _text
    return ""


async def _click_tab_element_by_text(page, tab_text: str) -> bool:
    """点击页面上文本为 tab_text 的 Tab 元素。

    P0-通用性(2026-07-28): 只用 [role=tab]:has-text()（W3C标准），移除框架特定类名。
    """
    if not tab_text:
        return False
    _esc = _escape_css_str(tab_text)
    _selectors = [
        f'[role="tab"]:has-text("{_esc}")',
        f'div[role="tab"]:has-text("{_esc}")',
        f'li[role="tab"]:has-text("{_esc}")',
    ]
    for _sel in _selectors:
        try:
            _loc = page.locator(_sel).first
            if await _loc.count() > 0:
                if await _loc.is_visible():
                    await _loc.click(timeout=5000)
                    _log.info(
                        "[TabSwitch] 点击 Tab '%s' 成功 (selector=%s)",
                        tab_text,
                        _sel[:50],
                    )
                    return True
        except Exception as _e:
            _log.debug("[TabSwitch] selector %s 点击失败: %s", _sel[:40], str(_e)[:60])
            continue
    return False


async def _click_tab_by_try_all(page, target_tab_value: str) -> bool:
    """兜底：遍历页面所有 Tab 元素，逐个点击并检测 URL tab 参数变化。

    P0-通用性(2026-07-28): 只用 [role=tab]（W3C标准），移除框架特定类名。
    """
    try:
        _pre_active_text = await _detect_current_active_tab_text(page)
        return await _try_click_tabs_in_selector(
            page, '[role="tab"]', target_tab_value, _pre_active_text
        )
    except Exception as _e:
        _log.debug("[TabSwitch] 遍历 Tab 元素异常: %s", str(_e)[:80])
        return False


async def _try_click_tabs_in_selector(
    page, selector: str, target_tab_value: str, pre_active_text: str
) -> bool:
    """遍历单个 selector 下的 Tab 元素，逐个点击检测是否切换成功。"""
    try:
        _loc = page.locator(selector)
        _count = await _loc.count()
        if _count == 0:
            return False
        for _i in range(min(_count, 10)):  # 最多试10个
            if await _try_click_one_tab(
                page, _loc, _i, target_tab_value, pre_active_text
            ):
                return True
    except Exception as _e:
        _log.debug("[tab_navigation] 操作异常: %s", _e, exc_info=True)
        return False


async def _try_click_one_tab(
    page, loc, idx: int, target_tab_value: str, pre_active_text: str
) -> bool:
    """点击单个 Tab 元素并验证切换结果（URL参数 or active文本变化）。"""
    try:
        _item = loc.nth(idx)
        if not await _item.is_visible():
            return False
        _pre_click_text = ""
        try:
            _pre_click_text = (await _item.text_content() or "").strip()
        except Exception as _e:
            _log.debug("[tab_navigation] text_content 读取异常: %s", _e, exc_info=True)
        await _item.click(timeout=3000)
        await asyncio.sleep(0.5)
        # 验证方式1: URL tab 参数 == 目标值（系统A/B）
        _cur_url = page.url or ""
        _cur_tab = (
            parse_qs(urlparse(_cur_url).query).get("tab", [""])[0] or ""
        ).strip()
        if _cur_tab == target_tab_value:
            return True
        # 验证方式2: 系统C（URL不变）—— active Tab 文本变化且等于点击的 Tab
        return await _check_system_c_tab_switch(
            page, _item, _pre_click_text, pre_active_text
        )
    except Exception:
        return False


async def _check_system_c_tab_switch(
    page, item, pre_click_text: str, pre_active_text: str
) -> bool:
    """系统C（URL不变）场景：通过 active Tab 文本变化判断切换成功。"""
    try:
        _sel_state = await item.get_attribute("aria-selected")
        if _sel_state == "true" and pre_click_text:
            _post_active_text = await _detect_current_active_tab_text(page)
            if (
                _post_active_text == pre_click_text
                and _post_active_text != pre_active_text
            ):
                return True
    except Exception as _e:
        _log.debug("[tab_navigation] 操作异常: %s", _e, exc_info=True)
        return False
