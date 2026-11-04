# -*- coding: utf-8 -*-
"""AI loop 类内辅助方法 Mixin — 从 _ai_loop.py 拆分（§4.3 单文件≤800行）。

职责：页面探索器反哺、滚动查找、多入口查找、library 快速路径、菜单回退。
被 _BrowserCoreSingleAiLoopMixin 多继承，方法通过 self. 调用，签名/方法体不变。
"""
import asyncio
from src.core.guarded import guard
import logging

_log = logging.getLogger(__name__)


class _AiLoopHelpersMixin:
    """AI loop 辅助方法能力。被 _BrowserCoreSingleAiLoopMixin 多继承。"""

    def _enrich_page_explorer(self, step: dict, resolved_element: dict, engine) -> None:
        """★ Phase 2: AI loop 成功定位后反哺页面探索器缓存。"""
        try:
            _page_explorer = getattr(self, "_page_explorer", None)
            if not _page_explorer or _page_explorer._explore_count <= 0:
                return
            _orig_hint = ""
            for _act in step.get("actions", []) or []:
                _h = _act.get("target_hint", "") if isinstance(_act, dict) else ""
                if _h:
                    _orig_hint = _h
                    break
            if not _orig_hint:
                return
            _page_url = engine._page.url if engine._page else ""
            _page_explorer.enrich_from_ai_success(
                _orig_hint, resolved_element, _page_url
            )
        except Exception as _e:
            _log.debug("[Phase2] enrich_page_explorer 异常: %s", _e, exc_info=True)

    async def _scroll_and_find(
        self, engine, keywords: list, dom_elements: list
    ) -> list:
        """升级策略1：滚动查找——模拟人类滚动页面查找元素。

        当前视口找不到时，向下滚动页面，重新获取DOM并匹配关键词。
        最多滚动3次，每次滚动一个视口高度。
        """
        if not keywords or not dom_elements:
            return []

        page = engine._page
        if not page:
            return []

        _keyword_set = {k.lower() for k in keywords}

        def _match_elements(elements):
            matched = []
            for el in elements:
                _el_text = (el.get("text", "") or "").lower()
                _el_placeholder = (el.get("placeholder", "") or "").lower()
                _el_label = (el.get("label", "") or "").lower()
                _el_role = (el.get("role", "") or "").lower()
                if any(
                    k in _el_text
                    or k in _el_placeholder
                    or k in _el_label
                    or k in _el_role
                    for k in _keyword_set
                ):
                    matched.append(el)
            return matched

        # 先检查当前DOM是否已有匹配（可能之前漏了）
        current_matched = _match_elements(dom_elements)
        if current_matched:
            return current_matched

        # 滚动查找
        for scroll_count in range(3):
            try:
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(500)  # 等待懒加载

                # 重新获取DOM
                new_elements = await engine.get_structured_state()
                if new_elements:
                    matched = _match_elements(new_elements)
                    if matched:
                        _log.info(
                            "    [滚动查找] 第 %d 次滚动找到 %d 个匹配元素",
                            scroll_count + 1,
                            len(matched),
                        )
                        return matched
            except Exception as _e:
                _log.debug("    [滚动查找] 第 %d 次滚动异常: %s", scroll_count + 1, _e)
                break

        # 滚回顶部
        try:
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(300)
        except Exception as _e:
            _log.debug("    [滚动查找] 滚回顶部异常: %s", _e, exc_info=True)

        return []

    async def _try_alternative_entries(
        self, engine, keywords: list, step_desc: str
    ) -> list:
        """升级策略2：多入口查找——从导航地图找其他可能入口。

        模拟人类行为：当前页面找不到，去其他菜单页面看看。
        从 system_nav_maps 表加载导航地图，匹配关键词相关的菜单项，
        导航到该页面后重新查找元素。
        最多尝试3个不同入口。
        """
        if not keywords:
            return []

        page = engine._page
        if not page:
            return []

        # 从数据库加载导航地图
        try:
            from src.core.config_loader import config as _cfg
            from src.users.project_auth import get_active_version_label

            # ★ P0-1 读侧迁移(2026-08-05): system_nav_maps(0行且无 nav_json 列) → nav_graph
            from src.pipeline.utils.nav_graph_read import read_menu_groups

            _project = getattr(_cfg, "project_name", "") or ""
            _version = get_active_version_label(_project) if _project else ""
            if not _project:
                return []

            # ★ 通用修复(2026-07-18): 导航地图使用 menu_groups 而非 flat_list
            # menu_groups[].items[] 包含 text/parent_path/url 字段
            menu_groups = read_menu_groups(_project, _version or None)
            if not menu_groups:
                _log.warning(
                    "[AILoop] nav_graph 无导航节点 (project=%s, version=%s)",
                    _project,
                    _version or "<latest>",
                )
                return []

            # 展平 menu_groups 为 flat_list
            flat_list = []
            for mg in menu_groups:
                for item in mg.get("items", []):
                    item_text = (item.get("text", "") or "").strip()
                    if not item_text:
                        continue
                    item_url = (item.get("url", "") or "").strip()
                    item_parent = (item.get("parent_path", "") or "").strip()
                    flat_list.append(
                        {
                            "text": item_text,
                            "url": item_url,
                            "path": item_parent.split("/") if item_parent else [],
                            "parent_path": item_parent,
                        }
                    )
            if not flat_list:
                return []

            # 当前URL
            current_url = page.url or ""

            # 用关键词匹配菜单项
            _keyword_set = {k.lower() for k in keywords}
            candidate_entries = []
            for item in flat_list:
                item_text = (item.get("text") or "").lower()
                item_url = item.get("url") or ""
                # 排除当前页面
                if item_url == current_url:
                    continue
                # 关键词匹配菜单文本
                if any(k in item_text for k in _keyword_set):
                    candidate_entries.append(item)

            # 如果关键词没匹配到菜单，用step_desc匹配
            if not candidate_entries and step_desc:
                _desc_lower = step_desc.lower()
                for item in flat_list:
                    item_text = (item.get("text") or "").lower()
                    item_url = item.get("url") or ""
                    if item_url == current_url:
                        continue
                    if item_text and item_text in _desc_lower:
                        candidate_entries.append(item)

            if not candidate_entries:
                _log.info("    [多入口查找] 导航地图中未找到匹配的入口")
                return []

            _log.info("    [多入口查找] 找到 %d 个候选入口", len(candidate_entries))

            # 尝试最多3个入口
            for entry in candidate_entries[:3]:
                entry_url = entry.get("url", "")
                entry_text = entry.get("text", "")

                if entry_url and entry_url.startswith(("http://", "https://")):
                    # 有URL：直接导航
                    try:
                        _log.info(
                            "    [多入口查找] 尝试入口(URL): %s (%s)",
                            entry_text,
                            entry_url,
                        )
                        await page.goto(
                            entry_url, wait_until="domcontentloaded", timeout=10000
                        )
                        await page.wait_for_timeout(1000)
                    except Exception as _e:
                        _log.debug(
                            "    [多入口查找] 入口 '%s' URL导航异常: %s", entry_text, _e
                        )
                        continue
                elif entry_text and entry.get("path"):
                    # ★ 通用修复(2026-07-18): SPA菜单无URL时，通过菜单路径逐级点击
                    menu_path = entry.get("path", [])
                    _log.info(
                        "    [多入口查找] 尝试入口(菜单): %s path=%s",
                        entry_text,
                        menu_path,
                    )
                    try:
                        if isinstance(menu_path, list) and len(menu_path) > 0:
                            from src.pipeline.steps.step4.browser_tools.execution._single import (
                                _fallback_menu_navigate,
                            )

                            nav_ok = await _fallback_menu_navigate(
                                engine, entry_text, entry_text
                            )
                            if nav_ok:
                                await page.wait_for_timeout(1000)
                            else:
                                continue
                        else:
                            continue
                    except Exception as _e:
                        _log.debug("    [多入口查找] 菜单导航异常: %s", _e)
                        continue
                else:
                    continue

                try:
                    # 获取新页面的DOM
                    new_elements = await engine.get_structured_state()
                    if not new_elements:
                        continue

                    # 匹配关键词
                    matched = []
                    for el in new_elements:
                        _el_text = (el.get("text", "") or "").lower()
                        _el_placeholder = (el.get("placeholder", "") or "").lower()
                        _el_label = (el.get("label", "") or "").lower()
                        _el_role = (el.get("role", "") or "").lower()
                        if any(
                            k in _el_text
                            or k in _el_placeholder
                            or k in _el_label
                            or k in _el_role
                            for k in _keyword_set
                        ):
                            matched.append(el)

                    if matched:
                        _log.info(
                            "    [多入口查找] ✓ 在入口 '%s' 找到 %d 个匹配元素",
                            entry_text,
                            len(matched),
                        )
                        return matched

                except Exception as _e:
                    _log.debug(
                        "    [多入口查找] 入口 '%s' 导航异常: %s", entry_text, _e
                    )
                    continue

            return []
        except Exception as _e:
            _log.debug("    [多入口查找] 整体异常: %s", _e)
            return []

    async def _try_library_fast_path(
        self, engine, action_type: str, action_params: dict
    ) -> tuple:
        """library_dispatch 快速路径（★ 归一化 2026-08-04：任意 action_id 先查表）。

        返回 (handled, success, error_msg)：
        - (True, True, "") — library 命中且成功，跳过 SelfHealing
        - (True, False, msg) — library 命中但失败（内部已做 SelfHealing），跳过 elif 避免重复
        - (False, False, "") — library 未命中或 ai_id= 格式，走 elif SelfHealing 兜底

        设计依据：system_design Part A §4.2。泛化原则：
        - 去掉 action_type ∈ (click, fill, navigate) 限制——任意动作先查表；
        - ai_id= 格式仍回退 elif（engine.click_element 不解析该前缀）；
        - 其余 action_params 字段透传给 dispatcher，供 _convert_action_to_tool_args 使用。
        """
        with guard("_try_library_fast_path: from src.pipeline.steps.step4.action", log=_log):
            from src.pipeline.steps.step4.action_dispatchers import (
                dispatch_action as _library_dispatch,
            )

            _sel = action_params.get("selector", "")
            # ★ ai_id= 格式交由 elif 处理（engine.click_element 不解析 ai_id= 前缀）
            if _sel.startswith("ai_id="):
                return False, False, ""
            _lib_action = {"action": action_type}
            if _sel:
                _lib_action["target_hint"] = _sel
            # ★ 泛化：将常见 action_params 字段透传给 dispatcher（_convert_action_to_tool_args 消费）
            _passthrough_keys = [
                "selector",
                "value",
                "text",
                "url",
                "option_text",
                "file_path",
                "key",
                "direction",
                "amount",
                "seconds",
                "duration_ms",
                "dx",
                "dy",
                "width",
                "height",
                "index",
                "url_keyword",
                "menu_item_hint",
                "save_dir",
                "delay_ms",
                "delta_x",
                "delta_y",
                "scale",
                "wait_until",
                "description",
                "distance",
                "focus_hint",
            ]
            for _k in _passthrough_keys:
                if _k in action_params and action_params[_k] is not None:
                    _lib_action[_k] = action_params[_k]
            _handled, _ok, _err_msg = await _library_dispatch(engine, _lib_action, None)
            if _handled:
                if _ok:
                    _log.info("    [AI] library_dispatch 快速路径成功: %s", action_type)
                    # 页面就绪等待已收敛到 run_action_with_hooks →
                    # settle_page_after_action（唯一权威），此处不再重复等待。
                else:
                    _log.info(
                        "    [AI] library_dispatch 命中但执行失败: %s | 详情: %s",
                        action_type,
                        _err_msg[:150],
                    )
                return _handled, _ok, _err_msg
        return False, False, ""

    async def _try_menu_fallback_for_fail(
        self, engine, step: dict, action_params: dict
    ) -> bool:
        """AI 判定 fail 时的菜单导航回退。

        当 AI 因当前页面找不到目标元素而判定 fail 时，
        尝试导航到目标系统首页并通过菜单查找目标元素。
        通用策略：不依赖具体业务术语，仅通过 URL hint 定位。
        返回 True 表示回退成功（页面已导航到正确位置），False 表示失败。
        """
        try:
            page = getattr(engine, "_page", None)
            if not page:
                return False
            from src.core.config_loader import config

            target_url = config.target_url or ""

            # ★ 从多个来源提取 hint：action_params、step 的 structured_actions、
            #   以及当前正在尝试的原始 action
            hint = (
                action_params.get("selector", "")
                or action_params.get("target_hint", "")
                or action_params.get("hint", "")
            )

            # ★ 如果 fail 的 action_params 没有 hint，从 step 中提取
            if not hint:
                _actions = step.get("structured_actions", []) or step.get("actions", [])
                for _a in _actions:
                    _h = _a.get("target_hint", "") or _a.get("selector", "")
                    if _h:
                        hint = _h
                        break

            if not hint:
                _log.info("[MenuFallback] 无 hint，跳过菜单回退")
                return False

            _log.info("[MenuFallback] 尝试菜单导航回退: hint=%s", hint[:80])

            # 1. 导航到目标系统首页
            if target_url:
                try:
                    await engine.goto(target_url)
                    from src.core.page_ready import wait_page_ready

                    await wait_page_ready(engine._page)
                except Exception as _e:
                    _log.warning("[MenuFallback] 导航到首页失败: %s", str(_e)[:80])
                    return False

            # 2. 尝试菜单导航
            from src.pipeline.steps.step4.browser_tools.execution._single import (
                _fallback_menu_navigate,
            )

            nav_ok = await _fallback_menu_navigate(engine, hint, hint)
            if nav_ok:
                _log.info("[MenuFallback] 菜单导航成功")
                return True

            _log.info("[MenuFallback] 菜单导航未找到匹配路径")
            return False
        except Exception as _e:
            _log.warning("[MenuFallback] 异常: %s", str(_e)[:120])
            return False
