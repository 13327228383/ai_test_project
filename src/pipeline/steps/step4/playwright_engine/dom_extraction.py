# -*- coding: utf-8 -*-
"""DOM 结构化提取模块。"""

import asyncio
import traceback
import logging

_log = logging.getLogger(__name__)

# ★ 截图硬超时（秒）：page.screenshot 在 Windows IOCP 上可无超时挂起（QA 2026-08-12）。
_SCREENSHOT_TIMEOUT_SEC: float = 15.0


class DomExtractionMixin:
    """智能剪枝与结构化数据提取。"""

    _page = None
    _last_original_count: int = 0

    async def get_structured_state(self, viewport_only: bool = False) -> list[dict]:
        """
        获取页面结构化状态。JS 遍历 DOM → 剪枝 → 保留交互元素属性 → 注入 data-ai-id + bbox。
        viewport_only=True 时仅返回视口内元素（省Token）。

        ★ 通用修复(2026-07-20 v6): 扩展 isInteractive() 识别 W3C ARIA 交互角色
          原bug: isInteractive() 只认 role='button'，不认 role='menuitem'/'tab'/'treeitem' 等，
                导致 Ant Design/ElementUI 侧边栏菜单、Tab 标签、树形控件全部被过滤掉。
                表现: 某SPA系统 导航到 /dataManage/portraitData 后只识别 17 个交互元素，
                     侧边栏的"辅材管理"菜单项 (<li role="menuitem">) 完全不可见，
                     AI 8 轮循环找不到目标元素，300s 超时。
          根因: isInteractive() 的判定逻辑过于狭隘，违背 W3C ARIA 规范。
                W3C 规范定义了多种交互角色，button 只是其中之一。
          修复: 1. INTERACTIVE_ROLES 集合包含所有 W3C ARIA 交互角色
                2. 识别常见 UI 框架的菜单项 class（.ant-menu-item / .el-menu-item 等）作为兜底
                3. 识别 [data-key] / [data-menu-id] 等业务自定义菜单属性
          通用性: 适用于任何遵循 W3C ARIA 规范的系统（Ant Design/ElementUI/Material UI/Bootstrap），
                  不针对特定行业或特定系统。
          合理性: 追根溯源到 isInteractive() 的判定逻辑缺陷，是治本方案。
          覆盖性: 同时影响 DOM 探索、AI prompt 构建、元素定位三条链路。
        """
        if not self._page:
            # ★ P0-fix(2026-07-24): 不再静默返回 []，否则调用方无法区分"页面无元素"与"page 失效"
            #   原bug: _page=None 时返回 [] → _ai_loop 进入重试 → 在 None 上调 wait_for_timeout 崩溃
            #   修复: 抛 RuntimeError 让所有调用方（18 处，均在 try 内）统一感知 page 失效
            #   通用性: 适用于任何"page 生命周期失效"场景（close_tab/浏览器崩溃/CDP 断连）
            #   合理性: 追根溯源到"沉默即失败"反模式（§5），不依赖调用方记得前置检查
            #   覆盖性: 覆盖所有调用 get_structured_state() 的代码路径
            raise RuntimeError(
                "engine._page is None; browser not initialized or page closed"
            )

        try:
            elements = await self._page.evaluate(
                """(_viewport_only) => {
                var SKIP_TAGS = new Set(['script', 'style', 'svg', 'noscript', 'template']);
                var INTERACTIVE_TAGS = new Set([
                    'button', 'a', 'input', 'select', 'textarea', 'option'
                ]);

                // ★ v6: W3C ARIA 交互角色集合（https://www.w3.org/TR/wai-aria-1.1/#role_definitions）
                //   覆盖所有可点击/可交互的角色，不止 button。
                var INTERACTIVE_ROLES = new Set([
                    'button', 'link', 'menuitem', 'menuitemcheckbox', 'menuitemradio',
                    'tab', 'treeitem', 'option', 'checkbox', 'radio', 'switch',
                    'slider', 'spinbutton', 'combobox', 'textbox', 'scrollbar'
                ]);

                // ★ v6: 常见 UI 框架的菜单项 class 模式（兜底识别）
                //   Ant Design: .ant-menu-item / .ant-menu-submenu-title
                //   ElementUI:  .el-menu-item / .el-submenu__title
                //   Antd Pro:   .ant-pro-menu-item
                //   Naive UI:   .n-menu-item
                function hasFrameworkMenuClass(el) {
                    var cls = el.getAttribute('class') || '';
                    if (!cls) return false;
                    // 避免误匹配：必须是菜单相关 class
                    if (/(^|\\s)(ant-menu-item|ant-menu-submenu-title|ant-pro-menu-item)(\\s|$)/.test(cls)) return true;
                    if (/(^|\\s)(el-menu-item|el-submenu__title)(\\s|$)/.test(cls)) return true;
                    if (/(^|\\s)(n-menu-item|n-submenu-title)(\\s|$)/.test(cls)) return true;
                    return false;
                }

                function isVisible(el) {
                    if (!el) return false;
                    var style = window.getComputedStyle(el);
                    if (style.display === 'none' ||
                        style.visibility === 'hidden' ||
                        style.visibility === 'collapse') return false;
                    if (el.offsetWidth === 0 && el.offsetHeight === 0) return false;
                    return true;
                }

                function getBoundingRect(el) {
                    var rect = el.getBoundingClientRect();
                    var scrollX = window.scrollX || window.pageXOffset || 0;
                    var scrollY = window.scrollY || window.pageYOffset || 0;
                    return {
                        x: Math.round(rect.x + scrollX),
                        y: Math.round(rect.y + scrollY),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height)
                    };
                }

                function isInViewport(bbox) {
                    var sx = window.scrollX || window.pageXOffset || 0;
                    var sy = window.scrollY || window.pageYOffset || 0;
                    return bbox.x < sx + window.innerWidth &&
                           bbox.x + bbox.width > sx &&
                           bbox.y < sy + window.innerHeight &&
                           bbox.y + bbox.height > sy;
                }

                function isInteractive(el) {
                    var tag = el.tagName.toLowerCase();
                    if (INTERACTIVE_TAGS.has(tag)) return true;
                    var role = el.getAttribute('role');
                    if (role && INTERACTIVE_ROLES.has(role)) return true;
                    if (el.hasAttribute('onclick')) return true;
                    if (el.hasAttribute('tabindex')) return true;
                    // ★ v6: UI 框架菜单项 class 兜底
                    if (hasFrameworkMenuClass(el)) return true;
                    return false;
                }

                var result = [];
                var aiIdCounter = 0;

                function traverse(node) {
                    if (node.nodeType !== 1) return;
                    var tag = node.tagName.toLowerCase();
                    if (SKIP_TAGS.has(tag)) return;
                    if (node.getAttribute('aria-hidden') === 'true') return;
                    if (!isVisible(node)) return;
                    if (tag === 'img') {
                        var alt = node.getAttribute('alt');
                        if (!alt || alt.replace(/\\s/g, '') === '') return;
                    }
                    if (!isInteractive(node)) {
                        for (var j = 0; j < node.children.length; j++) traverse(node.children[j]);
                        return;
                    }

                    var info = { tag: tag };
                    var text = (node.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (text.length > 0 && text.length < 200) info.text = text;
                    var domId = node.getAttribute('id');
                    if (domId) info.html_id = domId;
                    var cls = node.getAttribute('class');
                    if (cls) info.class = cls;
                    if (node.name !== undefined && node.name !== '') info.name = node.name;
                    var role = node.getAttribute('role');
                    if (role) info.role = role;
                    var ariaLabel = node.getAttribute('aria-label');
                    if (ariaLabel) info['aria-label'] = ariaLabel;
                    var href = node.getAttribute('href');
                    if (href) info.href = href;
                    if (node.value !== undefined && node.value !== '') info.value = node.value;
                    var placeholder = node.getAttribute('placeholder');
                    if (placeholder) info.placeholder = placeholder;
                    if (tag === 'input' && node.type) info.type = node.type;
                    else if (tag === 'textarea') info.type = 'textarea';
                    else if (tag === 'select') info.type = 'select';

                    aiIdCounter++;
                    var aiId = 'ai-' + aiIdCounter;
                    node.setAttribute('data-ai-id', aiId);
                    info.id = aiId;
                    info.bbox = getBoundingRect(node);

                    if (_viewport_only && !isInViewport(info.bbox)) {
                        for (var j = 0; j < node.children.length; j++) traverse(node.children[j]);
                        return;
                    }
                    result.push(info);
                    for (var j = 0; j < node.children.length; j++) traverse(node.children[j]);
                }

                var body = document.body;
                if (body) traverse(body);
                return {elements: result, total: aiIdCounter};
            }""",
                viewport_only,
            )

            mode_str = "viewport_only" if viewport_only else "full"
            # 提取原始元素总数（剪枝前）并附加到引擎实例
            total = (
                elements.get("total", 0)
                if isinstance(elements, dict)
                else len(elements)
            )
            self._last_original_count = total
            elements = (
                elements.get("elements", elements)
                if isinstance(elements, dict)
                else elements
            )
            return elements

        except Exception as e:
            traceback.print_exc()
            return []

    async def get_page_fingerprint(self) -> dict:
        """页面指纹 — URL + 标题 + 可见元素数，用于 SPA 变化检测。"""
        if not self._page:
            return {}
        try:
            url = self._page.url or ""
            title = await self._page.title() or ""
            count = await self._page.evaluate(
                "() => document.querySelectorAll('[data-ai-id]').length"
            )
            return {"url": url, "title": title, "ai_elements": count}
        except Exception as e:
            _log.debug("[fingerprint] 获取页面指纹失败: %s", e)
            return {}

    async def get_screenshot(self) -> bytes:
        """获取全屏截图(PNG)，失败返回 b""。

        ★ 防挂死：page.screenshot 在 Windows IOCP 上可无超时永久等待
        （QA 2026-08-12 全线程栈定位），此处加硬超时，超时返回 b""。
        """
        if not self._page:
            return b""
        try:
            return await asyncio.wait_for(
                self._page.screenshot(full_page=True),
                timeout=_SCREENSHOT_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            _log.warning(
                "[screenshot] 截图超时(%ds)，返回空", _SCREENSHOT_TIMEOUT_SEC
            )
            return b""
        except Exception as e:
            _log.warning("[screenshot] 截图失败: %s", e)
            return b""
