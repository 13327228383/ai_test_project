# -*- coding: utf-8 -*-
"""SelfHealing AI/视觉辅助 mixin — 从 self_healing.py 拆分（§4.3 单文件≤800行）。

职责：封装 AI 调用、视觉模型调用、DOM 摘要构造、截图、失败补丁记录为 mixin 类。
  - _build_dom_summary: 关键词筛选 + DOM 摘要文本构造
  - _take_screenshot_base64 / _take_screenshot_base64_async: 同步/异步截图
  - _try_visual_locator: 视觉模型元素定位（独立策略，非 _strategy_visual_similarity）
  - _call_ai_for_locator: 纯文本 AI 元素定位（走 router_module）
  - _call_vision_for_locator: 截图+DOM 双输入视觉模型定位（走 ai_brain.call_vision）
  - _record_failed_patch: AI 兜底失败时回写 verified=False 补丁

被 SelfHealingPlugin 多继承组装（参考 _self_healing_strategies_mixin 模式）。
依赖主类 __init__ 初始化的属性：self.config / self._stats
被 _SelfHealingStrategiesMixin._strategy_ai_locator 调用：
  - _build_dom_summary / _call_ai_for_locator / _take_screenshot_base64 / _record_failed_patch

根因(3层追问):
  1. 表层: self_healing.py 1045 行超 §4.3 红线
  2. 中层: 7 个 AI/视觉辅助方法内聚在 SelfHealingPlugin 类
  3. 根因: AI/视觉方法是独立职责（外部模型调用），应拆为 mixin 保持 self 访问 + 职责分离
通用性: 适用于所有系统/行业 — 纯 AI/视觉调用，不依赖业务术语
覆盖性: 影响 _strategy_ai_locator 兜底路径（最慢最贵的策略）
"""
from __future__ import (
    annotations,
)  # ★ 修复(2026-07-25): Python 3.9 兼容（PEP 604 `X | None` 语法）

import logging
from typing import Any

_log = logging.getLogger(__name__)


class _SelfHealingAiHelpersMixin:
    """SelfHealing AI/视觉辅助 mixin — AI 调用 + 视觉模型 + DOM 摘要 + 截图 + 失败补丁。

    ★ 架构债清理(2026-07-25): 从 self_healing.py 拆分，主类通过多继承组装。
    依赖主类 __init__ 初始化：self.config / self._stats
    被 _SelfHealingStrategiesMixin._strategy_ai_locator 调用。
    """

    def _build_dom_summary(
        self, elements: list[dict], keywords: list[str] = None
    ) -> str:
        """构造 DOM 元素摘要——关键词精准筛选，非全量100个元素。

        参数:
            elements: 全量DOM元素列表
            keywords: 关键词列表，如果提供则只返回匹配的元素
        """
        # ★ 关键词筛选：只保留与目标相关的元素
        if keywords:
            _keyword_set = {k.lower() for k in keywords}
            filtered = []
            for el in elements:
                if not isinstance(el, dict):
                    continue
                _el_text = (el.get("text", "") or "").lower()
                _el_placeholder = (el.get("placeholder", "") or "").lower()
                _el_label = (el.get("label", "") or "").lower()
                _el_role = (el.get("role", "") or "").lower()
                _el_aria = (
                    el.get("ariaLabel", "") or el.get("aria-label", "") or ""
                ).lower()
                if any(
                    k in _el_text
                    or k in _el_placeholder
                    or k in _el_label
                    or k in _el_role
                    or k in _el_aria
                    for k in _keyword_set
                ):
                    filtered.append(el)
            elements = filtered
            if not elements:
                return ""  # 关键词无匹配，返回空
            _log.info(
                "[SelfHealing] 关键词筛选: %d → %d 个相关元素",
                len(elements),
                len(filtered),
            )

        lines = []
        for i, el in enumerate(elements[:20]):  # ★ 最多20个相关元素（非100个）
            if not isinstance(el, dict):
                continue
            parts = []
            tag = el.get("tag", "")
            if tag:
                parts.append(f"tag={tag}")
            text = el.get("text", "")
            if text:
                parts.append(f"text={text[:50]}")
            placeholder = el.get("placeholder", "")
            if placeholder:
                parts.append(f"placeholder={placeholder[:50]}")
            label = el.get("label", "")
            if label:
                parts.append(f"label={label[:50]}")
            role = el.get("role", "")
            if role:
                parts.append(f"role={role}")
            name = el.get("name", "")
            if name:
                parts.append(f"name={name[:30]}")
            html_id = el.get("id", "") or el.get("html_id", "")
            if html_id:
                parts.append(f"id={html_id[:30]}")
            css_class = el.get("class", "")
            if css_class:
                parts.append(f"class={css_class[:50]}")
            aria_label = el.get("ariaLabel", "") or el.get("aria-label", "")
            if aria_label:
                parts.append(f"aria-label={aria_label[:50]}")
            if parts:
                lines.append(f"[{i}] " + " | ".join(parts))
        return "\n".join(lines)

    async def _take_screenshot_base64_async(self, engine) -> str:
        """获取当前页面截图的base64编码（async版本）。"""
        try:
            page = getattr(engine, "_page", None)
            if not page:
                return ""
            screenshot = await page.screenshot(full_page=False)
            import base64 as _b64

            return _b64.b64encode(screenshot).decode("ascii")
        except Exception as _e:
            _log.debug("[SelfHealing] 截图失败: %s", _e)
            return ""

    def _take_screenshot_base64(self, engine) -> str:
        """同步获取截图（从async上下文外调用）。"""
        try:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(self._take_screenshot_base64_async(engine))
            # 已经在事件循环中，使用线程池避免 run_until_complete
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(
                    asyncio.run, self._take_screenshot_base64_async(engine)
                ).result(timeout=5)
        except Exception as _e:
            _log.debug("[SelfHealing] 同步截图失败: %s", _e)
            return ""

    def _try_visual_locator(self, engine, keyword: str) -> str | None:
        """策略3优化：截图+视觉模型定位元素（非仅click，通用定位）。

        截图后用视觉模型识别目标元素位置，返回选择器而非坐标。
        """
        try:
            from src.core.config_loader import config

            if not getattr(config, "vision_model_enabled", False):
                return None
            vision_provider = getattr(config, "vision_provider", "")
            if vision_provider == "ollama":
                return None  # Ollama无chat_vision实现

            screenshot_b64 = self._take_screenshot_base64(engine)
            if not screenshot_b64:
                return None

            from src.ai.ai_brain import ai_brain

            prompt = (
                f"在截图中找到与「{keyword}」相关的元素。"
                f"只返回该元素的文本或placeholder或label（用于定位），格式为选择器：\n"
                f"- text=元素文本\n"
                f"- placeholder=占位符\n"
                f"- label=标签文本\n"
                f"如果没有找到，返回: NONE"
            )
            result = ai_brain.call_vision(
                image_base64=screenshot_b64,
                prompt=prompt,
                caller_name="visual_locator",
            )
            if not result or not result.get("success"):
                return None
            resp = (result.get("data") or "").strip()
            if resp.upper() == "NONE" or not resp:
                return None
            # 校验返回格式
            for prefix in ("text=", "placeholder=", "label=", "role=", "css=", "name="):
                if resp.startswith(prefix):
                    return resp
            return None
        except Exception as _e:
            _log.debug("[SelfHealing] 视觉定位异常: %s", _e)
            return None

    def _call_ai_for_locator(
        self, keyword: str, dom_summary: str, model_type: str, screenshot_b64: str = ""
    ) -> str | None:
        """调用 AI 将自然语言关键词映射到 DOM 选择器（走统一路由）。

        参数:
            keyword: 自然语言关键词（如"用户名输入框"）
            dom_summary: DOM 元素摘要文本（已关键词筛选）
            model_type: "verification"（校验模型）或 "general"（任务模型）
            screenshot_b64: 截图base64（可选，提供时走视觉模型增强定位）

        返回:
            选择器字符串（如 "placeholder=请输入用户名"）或 None
        """
        # ★ 有截图时走视觉模型，无截图时走纯文本路由
        if screenshot_b64:
            return self._call_vision_for_locator(
                keyword, dom_summary, screenshot_b64, model_type
            )

        prompt = (
            "你是浏览器元素定位专家。请根据自然语言描述，从 DOM 元素列表中找到最匹配的元素，"
            "并返回其选择器。\n\n"
            f"## 自然语言描述\n{keyword}\n\n"
            f"## DOM 元素列表\n{dom_summary}\n\n"
            "## 输出格式（严格单一行，不要解释）\n"
            "返回以下格式之一：\n"
            "- text=元素文本（如 text=登录）\n"
            "- placeholder=占位符文本（如 placeholder=请输入用户名）\n"
            "- label=标签文本（如 label=用户名）\n"
            "- role=ARIA角色（如 role=textbox）\n"
            "- css=.类名（如 css=.login-btn）\n"
            "- name=表单字段名（如 name=username）\n\n"
            "## 规则\n"
            "1. 只返回一个最匹配的选择器，不要多个\n"
            "2. 不要返回解释、分析或其他文本\n"
            "3. 如果没有匹配元素，返回: NONE\n"
        )

        try:
            from src.ai.router_module import router

            result = router.route(
                prompt=prompt,
                system_prompt="你是浏览器元素定位专家，只返回选择器，不解释。",
                task_type=model_type,
                expect_json=False,
                temperature=0.1,
                max_tokens=2048,
            )
            if not result or not result.get("success"):
                _log.debug(
                    "[SelfHealing] AI 调用失败: %s",
                    (result or {}).get("error", "unknown"),
                )
                return None
            resp = (result.get("data") or "").strip()
            if resp.upper() == "NONE" or not resp:
                return None
            # 校验返回格式
            for prefix in (
                "text=",
                "placeholder=",
                "label=",
                "role=",
                "css=",
                "ai_id=",
                "name=",
            ):
                if resp.startswith(prefix):
                    return resp
            # 格式不合规，尝试提取选择器
            import re as _re

            _selector_match = _re.search(
                r"(text|placeholder|label|role|css|ai_id|name)=[\u4e00-\u9fff\w\-\.]+",
                resp,
            )
            if _selector_match:
                _log.debug(
                    "[SelfHealing] AI 返回格式不合规，但提取到选择器: %s",
                    _selector_match.group(),
                )
                return _selector_match.group()
            _log.debug("[SelfHealing] AI 返回格式不合规: %s", resp[:80])
            return None
        except Exception as _e:
            _log.debug("[SelfHealing] AI 调用异常: %s", _e, exc_info=True)
            return None

    def _call_vision_for_locator(
        self, keyword: str, dom_summary: str, screenshot_b64: str, model_type: str
    ) -> str | None:
        """策略3+4融合：截图+DOM摘要一起给视觉模型，双重信息辅助定位。

        参数:
            keyword: 自然语言关键词
            dom_summary: 精简DOM元素摘要（已关键词筛选）
            screenshot_b64: 页面截图base64
            model_type: 模型类型
        """
        import concurrent.futures

        try:
            from src.ai.ai_brain import ai_brain

            prompt = (
                f"你是浏览器元素定位专家。请根据截图和DOM元素列表，找到与「{keyword}」最匹配的元素。\n\n"
                f"## DOM 元素列表\n{dom_summary}\n\n"
                "## 输出格式（严格单一行）\n"
                "返回以下格式之一：\n"
                "- text=元素文本\n"
                "- placeholder=占位符\n"
                "- label=标签文本\n"
                "- role=ARIA角色\n"
                "- css=.类名\n"
                "- name=字段名\n"
                "如果没有匹配，返回: NONE\n"
            )
            # ★ FIX (2026-07-11): call_vision 是同步阻塞调用，在 asyncio 事件循环中会阻塞所有协程。
            #   用 ThreadPoolExecutor 包装 + 30s 超时，避免 Step4 整体卡死。
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _executor:
                _future = _executor.submit(
                    ai_brain.call_vision,
                    image_base64=screenshot_b64,
                    prompt=prompt,
                    caller_name="vision_locator",
                )
                result = _future.result(timeout=30)
            if not result or not result.get("success"):
                _log.debug(
                    "[SelfHealing] 视觉模型调用失败: %s",
                    (result or {}).get("error", "unknown"),
                )
                return None
            resp = (result.get("data") or "").strip()
            if resp.upper() == "NONE" or not resp:
                return None
            # 校验返回格式
            for prefix in (
                "text=",
                "placeholder=",
                "label=",
                "role=",
                "css=",
                "ai_id=",
                "name=",
            ):
                if resp.startswith(prefix):
                    return resp
            # 格式不合规，尝试提取
            import re as _re

            _selector_match = _re.search(
                r"(text|placeholder|label|role|css|ai_id|name)=[\u4e00-\u9fff\w\-\.]+",
                resp,
            )
            if _selector_match:
                return _selector_match.group()
            return None
        except concurrent.futures.TimeoutError:
            _log.warning("[SelfHealing] 视觉模型定位超时(30s): keyword=%s", keyword)
            return None
        except Exception as _e:
            _log.debug("[SelfHealing] 视觉模型定位异常: %s", _e)
            return None

    def _record_failed_patch(self, target_hint: str, keyword: str):
        """回写 verified=False 补丁，避免下次重复调用 AI 浪费 token。"""
        try:
            from src.core.db_manager import db_manager
            from src.core.config_loader import config as _cfg

            project = getattr(_cfg, "project_name", "") or ""
            db = db_manager.get_db()
            cursor = db.cursor()
            sql = (
                "INSERT INTO self_heal_patches "
                "(project, original_hint, healed_hint, strategy, verified, case_id, page_url, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())"
            )
            cursor.execute(
                sql, (project, target_hint[:200], "", "ai_locator_failed", 0, "", "")
            )
            db.commit()
            _log.info("[SelfHealing] 已记录 AI 兜底失败补丁: %s", keyword)
        except Exception as _e:
            _log.error("[SelfHealing] 失败补丁记录异常: %s", _e, exc_info=True)
