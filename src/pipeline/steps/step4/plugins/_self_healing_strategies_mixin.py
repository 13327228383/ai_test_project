# -*- coding: utf-8 -*-
"""SelfHealing 策略 mixin — 从 self_healing.py 拆分（§4.3 单文件≤800行）。

职责：封装 9 种元素定位策略 + 调度器 + 关键词提取为 mixin 类。
  - _try_strategy: 策略调度器（按策略名路由到具体 _strategy_* 方法）
  - _extract_keywords / _extract_reasoning_keywords: 关键词提取
  - _strategy_semantic_text: 语义文本匹配
  - _strategy_partial_text: 部分文本双向匹配
  - _strategy_nearby_element: 邻近元素推断
  - _strategy_css_simplify: CSS 选择器简化（去状态类）
  - _strategy_css_class: CSS 类名匹配
  - _strategy_role_attribute: ARIA role 匹配
  - _strategy_type_fallback: 类型兜底（input/button 首个匹配）
  - _strategy_visual_similarity: 视觉相似度（预留）
  - _strategy_ai_locator: AI 语义映射兜底（调用 _SelfHealingAiHelpersMixin 方法）

被 SelfHealingPlugin 多继承组装（参考 _ai_loop_helpers_mixin 模式）。
依赖主类 __init__ 初始化的属性：self._type_hints / self._role_map / self.config / self._stats

根因(3层追问):
  1. 表层: self_healing.py 1045 行超 §4.3 红线
  2. 中层: 9 个策略方法 + 2 个关键词提取全内聚在 SelfHealingPlugin 类
  3. 根因: 策略方法本质独立，应拆为 mixin 保持 self 访问 + 职责分离
通用性: 适用于所有系统/行业 — 纯元素定位策略，不依赖业务术语
覆盖性: 影响所有 SelfHealing 策略调度路径
"""
from __future__ import (
    annotations,
)  # ★ 修复(2026-07-25): Python 3.9 兼容（PEP 604 `X | None` 语法）

import logging
from typing import Any

_log = logging.getLogger(__name__)


class _SelfHealingStrategiesMixin:
    """SelfHealing 策略 mixin — 9 种元素定位策略 + 调度器 + 关键词提取。

    ★ 架构债清理(2026-07-25): 从 self_healing.py 拆分，主类通过多继承组装。
    依赖主类 __init__ 初始化：self._type_hints / self._role_map / self.config / self._stats
    """

    def _try_strategy(
        self,
        strategy: str,
        target_hint: str,
        ai_id: str,
        page_state: dict[str, Any],
        engine,
    ) -> dict[str, Any] | None:
        """尝试单一回退策略。"""
        elements = page_state.get("elements", [])

        if strategy == "semantic_text":
            return self._strategy_semantic_text(target_hint, elements)
        elif strategy == "css_simplify":
            return self._strategy_css_simplify(target_hint, elements)
        elif strategy == "partial_text":
            return self._strategy_partial_text(target_hint, elements)
        elif strategy == "nearby_element":
            return self._strategy_nearby_element(ai_id, elements)
        elif strategy == "css_class":
            return self._strategy_css_class(target_hint, elements)
        elif strategy == "role_attribute":
            return self._strategy_role_attribute(target_hint, elements)
        elif strategy == "type_fallback":
            return self._strategy_type_fallback(target_hint, elements)
        elif strategy == "visual_similarity":
            return self._strategy_visual_similarity(target_hint, ai_id, engine)
        elif strategy == "ai_locator":
            return self._strategy_ai_locator(target_hint, elements, engine)
        return None

    def _extract_keywords(self, target_hint: str) -> list[str]:
        """从 hint 中提取搜索关键词，支持带前缀和裸文本两种格式。"""
        import re

        # 1. 尝试带前缀格式: text=xxx, placeholder=xxx, label=xxx
        for prefix in ("text", "placeholder", "label"):
            m = re.findall(r'%s=["\']?([\u4e00-\u9fff\w]+)' % prefix, target_hint)
            if m:
                return m
        # 2. ★ 裸文本: 直接用 hint 全文作为关键词（去掉 CSS 选择器前缀）
        if target_hint.startswith("#"):
            # CSS ID 选择器如 #ai-2 → 提取 ai_id 已在调用方处理，这里返回空不干扰文本匹配
            # 但对于 #搜索输入框 这种带文本的，还是保留文本
            cleaned = target_hint[1:].strip()
            if cleaned and len(cleaned) >= 2:
                return [cleaned]
            return []
        if target_hint.startswith(".") or target_hint.startswith("["):
            return []
        # 去掉常见前缀后作为关键词
        cleaned = re.sub(r"^(css=|role=|ai_id=)", "", target_hint).strip().strip("\"'")
        if cleaned and len(cleaned) >= 2:
            return [cleaned]
        return []

    def _extract_reasoning_keywords(
        self, reasoning: str, action_type: str = "click"
    ) -> list[str]:
        """从 AI reasoning 文本中提取可能的目标元素关键词。

        用于 #ai-N 选择器失效后，通过语义文本重新定位目标元素。
        与 AI 循环内部的 reasoning 提取逻辑保持一致，但集中到 SelfHealing 单一入口。
        """
        if not reasoning:
            return []
        import re as _re

        keywords = []
        # 1. 匹配各种引号包裹的中文/英文词
        _m = _re.search(
            r'[\u2018\u2019\u201c\u201d\'"]([\u4e00-\u9fff\w]+)[\u2018\u2019\u201c\u201d\'"]',
            reasoning,
        )
        _text_kw = _m.group(1) if _m else None
        if _text_kw:
            keywords.append(_text_kw)
            return keywords
        # 2. 无引号回退：按动作类型匹配常见动词模式
        verb_patterns = {
            "click": [
                r"点击([\u4e00-\u9fff]{2,6})",
                r"展开([\u4e00-\u9fff]{2,6})",
                r"进入([\u4e00-\u9fff]{2,6})",
                r"查找([\u4e00-\u9fff]{2,6})",
                r"寻找([\u4e00-\u9fff]{2,6})",
                r"选择([\u4e00-\u9fff]{2,6})",
                r"打开([\u4e00-\u9fff]{2,6})",
            ],
            "fill": [
                r"输入([\u4e00-\u9fff]{2,6})",
                r"填写([\u4e00-\u9fff]{2,6})",
                r"填入([\u4e00-\u9fff]{2,6})",
                r"查找([\u4e00-\u9fff]{2,6})",
                r"寻找([\u4e00-\u9fff]{2,6})",
                r"选择([\u4e00-\u9fff]{2,6})",
                r"点击([\u4e00-\u9fff]{2,6})",
            ],
        }
        for pat in verb_patterns.get(action_type, []):
            _m2 = _re.search(pat, reasoning)
            if _m2:
                keywords.append(_m2.group(1))
                break
        return keywords

    def _strategy_semantic_text(
        self, target_hint: str, elements: list[dict]
    ) -> dict[str, Any] | None:
        """语义文本匹配：提取 hint 中的关键词，在元素文本中模糊匹配。"""
        keywords = self._extract_keywords(target_hint)
        if not keywords:
            return None

        keyword = keywords[0].lower()
        for elem in elements:
            elem_text = (elem.get("text", "") or "").lower()
            elem_placeholder = (elem.get("placeholder", "") or "").lower()
            elem_label = (elem.get("label", "") or "").lower()

            if (
                keyword in elem_text
                or keyword in elem_placeholder
                or keyword in elem_label
            ):
                new_hint = elem.get("text", "") or elem.get("placeholder", "")
                return {"new_hint": f"text={new_hint}"}

        return None

    def _strategy_partial_text(
        self, target_hint: str, elements: list[dict]
    ) -> dict[str, Any] | None:
        """部分文本匹配：hint 中的文本与元素文本双向子串匹配。"""
        keywords = self._extract_keywords(target_hint)
        if not keywords:
            return None

        keyword = keywords[0]
        for elem in elements:
            elem_text = elem.get("text", "") or ""
            elem_placeholder = elem.get("placeholder", "") or ""
            # 双向包含：keyword 包含元素文本 或 元素文本包含 keyword
            for elem_val in [elem_text, elem_placeholder]:
                if not elem_val or len(elem_val) < 2:
                    continue
                if keyword in elem_val or elem_val in keyword:
                    return {"new_hint": f"text={elem_val}"}
        return None

    def _strategy_nearby_element(
        self, ai_id: str, elements: list[dict]
    ) -> dict[str, Any] | None:
        """邻近元素推断：通过 ai-id 的相邻元素推断目标。"""
        if not ai_id or not ai_id.startswith("ai-"):
            return None

        try:
            target_idx = int(ai_id.split("-")[1])
        except (ValueError, IndexError) as _e:
            _log.debug("[self_healing] AI ID 解析失败(§5): %s", _e)
            return None

        # 找相邻元素中可交互的
        for offset in [-1, 1, -2, 2]:
            neighbor_id = f"ai-{target_idx + offset}"
            for elem in elements:
                if elem.get("ai_id") == neighbor_id and elem.get("interactive"):
                    return {"new_hint": f"ai_id={neighbor_id}"}
        return None

    def _strategy_css_simplify(
        self, target_hint: str, elements: list[dict]
    ) -> dict[str, Any] | None:
        """★ CSS 选择器简化：去掉状态类（-selected, -active, -disabled 等）后重试。

        场景：AI 生成的 CSS 选择器包含状态类，如 li.ant-menu-item-selected.ant-menu-item-only-child，
        但目标元素不在该状态时，选择器无法匹配。简化后如 li.ant-menu-item 可匹配所有同类型元素。
        """
        import re

        if not target_hint or not elements:
            return None
        # 仅处理 CSS 选择器（包含 .class 或 #id 或 tag.class 格式）
        if "." not in target_hint and "#" not in target_hint:
            return None
        # 去掉状态相关的类：-selected, -active, -disabled, -focused, -hover, -open, -only-child, -first-child, -last-child
        _state_patterns = [
            r"\.[a-zA-Z0-9_-]*-selected\b",
            r"\.[a-zA-Z0-9_-]*-active\b",
            r"\.[a-zA-Z0-9_-]*-disabled\b",
            r"\.[a-zA-Z0-9_-]*-focused\b",
            r"\.[a-zA-Z0-9_-]*-hover\b",
            r"\.[a-zA-Z0-9_-]*-open\b",
            r"\.[a-zA-Z0-9_-]*-only-child\b",
            r"\.[a-zA-Z0-9_-]*-first-child\b",
            r"\.[a-zA-Z0-9_-]*-last-child\b",
            r"\.ant-menu-item-selected\b",
            r"\.ant-menu-submenu-active\b",
            r"\.ant-menu-submenu-open\b",
        ]
        simplified = target_hint
        for pat in _state_patterns:
            simplified = re.sub(pat, "", simplified)
        # 清理多余空格
        simplified = re.sub(r"\s+", " ", simplified).strip()
        if simplified == target_hint:
            return None  # 没有可简化的状态类
        _log.info(
            "[SelfHealing] CSS 简化: '%s' → '%s'", target_hint[:80], simplified[:80]
        )
        return {"new_hint": simplified}

    def _strategy_css_class(
        self, target_hint: str, elements: list[dict]
    ) -> dict[str, Any] | None:
        """CSS 类名匹配：通过类名推断元素类型（使用配置化关键词）。"""
        # 从 hint 推断元素类型（使用 self._type_hints，避免硬编码业务术语）
        inferred_type = None
        hint_lower = target_hint.lower()
        for elem_type, keywords in self._type_hints.items():
            if any(kw in hint_lower or kw in target_hint for kw in keywords):
                inferred_type = elem_type
                break

        if not inferred_type:
            return None

        # ★ 防过度泛化：如果 hint 去掉所有类型关键词后仍有具体文本，
        #   不应降级为通用 CSS（如"新建配方按钮"→css=.button 会丢失"新建配方"，
        #   匹配到页面上任意 button 元素，几乎总是错误匹配）。
        #   只有 hint 本身只是类型描述（如"按钮"、"button"）时才使用此策略。
        import re as _re

        hint_without_type = target_hint
        for keywords in self._type_hints.values():
            for kw in keywords:
                hint_without_type = hint_without_type.replace(kw, "")
        # 去掉常见分隔符和空白，判断是否还有剩余语义文本
        hint_without_type = _re.sub(r"[\s=:\-_.()\[\]\{\}]+", "", hint_without_type)
        if hint_without_type:
            _log.debug(
                "[SelfHealing] ✗ css_class 策略跳过（hint 含具体文本 '%s'，降级会丢失语义）: %s",
                hint_without_type[:20],
                target_hint[:40],
            )
            return None

        for elem in elements:
            elem_classes = (elem.get("class", "") or "").lower()
            elem_tag = (elem.get("tag", "") or "").lower()
            if inferred_type in elem_classes:
                return {"new_hint": f"css=.{inferred_type}"}
            if inferred_type == elem_tag:
                return {"new_hint": f"css={inferred_type}"}
        return None

    def _strategy_role_attribute(
        self, target_hint: str, elements: list[dict]
    ) -> dict[str, Any] | None:
        """ARIA role 匹配：通过无障碍属性和标签名定位（使用配置化关键词）。"""
        # 使用 self._role_map，避免在代码里硬编码业务术语
        target_role = None
        hint_lower = target_hint.lower()
        for role, keywords in self._role_map.items():
            if any(kw in hint_lower or kw in target_hint for kw in keywords):
                target_role = role
                break

        if not target_role:
            return None

        for elem in elements:
            elem_role = elem.get("role", "") or ""
            elem_tag = (elem.get("tag", "") or "").lower()
            # ★ 不仅看 role 属性，还看标签名（button 标签隐式 role=button）
            if elem_role == target_role:
                return {"new_hint": f"role={target_role}"}
            # 标签名直接匹配
            tag_role_map = {
                "button": "button",
                "input": "textbox",
                "textarea": "textbox",
                "select": "combobox",
                "a": "link",
            }
            if tag_role_map.get(elem_tag) == target_role and elem.get("interactive"):
                return {"new_hint": f"role={target_role}"}
        return None

    def _strategy_type_fallback(
        self, target_hint: str, elements: list[dict]
    ) -> dict[str, Any] | None:
        """★ 类型兜底：当文本/class/role 全失败时，按 hint 语义类型匹配第一个同类型元素。

        例如 "输入框" → 第一个 input/textarea/textbox，"提交按钮" → 第一个 button/clickable。
        避免 6 种规则策略全失败后必然触发 AI 兜底（最慢最贵）。
        """
        # 1. 从 hint 推断目标元素类型（复用 _type_hints 配置，不硬编码）
        inferred_type = None
        hint_lower = target_hint.lower()
        for elem_type, keywords in self._type_hints.items():
            if any(kw in hint_lower or kw in target_hint for kw in keywords):
                inferred_type = elem_type
                break

        if not inferred_type:
            _log.info(
                "[SelfHealing] type_fallback: 无法从 hint='%s' 推断类型", target_hint
            )
            return None

        _log.info(
            "[SelfHealing] type_fallback: hint='%s'→inferred_type='%s', elements=%d",
            target_hint,
            inferred_type,
            len(elements),
        )

        # 2. 按类型匹配第一个符合条件的元素
        #    tag 优先级映射：type → [tag1, tag2, ...]  配置化，避免硬编码
        _tag_priority = self.config.get(
            "tag_priority",
            {
                "input": ["input", "textarea"],
                "textarea": ["textarea", "input"],
                "button": ["button", "a"],
                "select": ["select"],
                "checkbox": ["input"],
            },
        )

        # 优先匹配 interactable 元素，其次匹配任何同类型元素
        for match_interactive in (True, False):
            for elem in elements:
                elem_tag = (elem.get("tag", "") or "").lower()
                elem_role = (elem.get("role", "") or "").lower()
                elem_type_attr = (elem.get("type", "") or "").lower()

                # tag 匹配
                if elem_tag in _tag_priority.get(inferred_type, []):
                    if not match_interactive or elem.get("interactive"):
                        _log.info(
                            "[SelfHealing] ✓ type_fallback: '%s'→tag=%s",
                            target_hint,
                            elem_tag,
                        )
                        return {"new_hint": f"tag={elem_tag}"}

                # role 匹配（复用 _role_map）
                if inferred_type == "input" and elem_role in ("textbox", "searchbox"):
                    if not match_interactive or elem.get("interactive"):
                        _log.info(
                            "[SelfHealing] ✓ type_fallback: '%s'→role=%s",
                            target_hint,
                            elem_role,
                        )
                        return {"new_hint": f"role={elem_role}"}
                if inferred_type == "button" and elem_role in ("button", "link"):
                    if not match_interactive or elem.get("interactive"):
                        _log.info(
                            "[SelfHealing] ✓ type_fallback: '%s'→role=%s",
                            target_hint,
                            elem_role,
                        )
                        return {"new_hint": f"role={elem_role}"}

                # checkbox 特殊处理：type=checkbox
                if inferred_type == "checkbox" and elem_type_attr == "checkbox":
                    if not match_interactive or elem.get("interactive"):
                        _log.info(
                            "[SelfHealing] ✓ type_fallback: '%s'→type=checkbox",
                            target_hint,
                        )
                        return {"new_hint": "type=checkbox"}

        _log.info(
            "[SelfHealing] ✗ type_fallback: 无匹配元素 (type=%s, elements=%d, tags=%s)",
            inferred_type,
            len(elements),
            [e.get("tag", "?") for e in elements[:10]],
        )
        # ★ Fix (2026-07-09): 页面状态无法捕获元素时（如 Shadow DOM / 动态组件），
        #   回退到直接 CSS 选择器，Playwright 引擎可以穿透 Shadow DOM 和动态渲染。
        _css_fallback = {
            "input": "css=input, textarea, [contenteditable]",
            "textarea": "css=textarea, input",
            "button": "css=button, a, [role=button]",
            "select": "css=select",
            "checkbox": "css=input[type=checkbox]",
        }
        if inferred_type in _css_fallback:
            _log.info(
                "[SelfHealing] type_fallback: 回退到 CSS 选择器 %s",
                _css_fallback[inferred_type],
            )
            return {"new_hint": _css_fallback[inferred_type]}
        return None

    def _strategy_visual_similarity(
        self, target_hint: str, ai_id: str, engine
    ) -> dict[str, Any] | None:
        """视觉相似度：通过多模态模型判断元素相似性（预留接口）。"""
        # 当前实现为预留，需要多模态模型支持
        _log.debug("[SelfHealing] 视觉相似度策略需要多模态模型支持，跳过")
        return None

    def _strategy_ai_locator(
        self, target_hint: str, elements: list[dict], engine
    ) -> dict[str, Any] | None:
        """★ AI 语义映射兜底：6种规则策略全失败后，调用云 AI 分析 DOM 快照，
        将自然语言 hint（如"用户名输入框"）映射到具体元素选择器（如 placeholder=请输入用户名）。

        策略：
        1. 先用关键词精准筛选相关元素（非全量100个元素）
        2. 筛选结果给校验模型（速度快、成本低）
        3. 失败时升级到任务模型（reasoning model），附带截图辅助
        4. 仍失败时回写 verified=False 补丁，避免下次重复调用浪费 token

        依赖 _SelfHealingAiHelpersMixin 提供：
          - _build_dom_summary / _call_ai_for_locator / _take_screenshot_base64 / _record_failed_patch
        """
        if not target_hint or not elements:
            return None

        # ★ 提取核心关键词（去掉前缀）
        keywords = self._extract_keywords(target_hint)
        if not keywords:
            return None
        keyword = keywords[0]
        _log.info(
            "[SelfHealing] AI 兜底策略启动: hint='%s' keyword='%s' 元素数=%d",
            target_hint,
            keyword,
            len(elements),
        )

        # ★ 关键词精准筛选：只给AI相关元素，不给全量100个
        dom_summary = self._build_dom_summary(elements, keywords)

        # 如果关键词筛选无结果，回退到全量元素给 AI 分析
        if not dom_summary:
            # ★ Fix16 (2026-07-07): 关键词无匹配时不应该跳过 AI 兜底
            #   原行为：关键词无匹配 → 直接返回 None（导致所有用例失败）
            #   新行为：关键词无匹配 → 用全量元素（前20个）给 AI 分析
            #   理由：关键词无匹配可能是因为 target_hint 用了通用描述（如"查询输入框"），
            #         但页面上有相关元素（如 placeholder="请输入搜索关键词"）。
            #         AI 能够理解语义，把"查询输入框"映射到正确的元素。
            #         日志显示 元素数=6，说明页面上有元素，只是关键词没匹配到。
            #   违反契约：§11.3 "元素定位失败时必须走 AI fallback"
            _log.info("[SelfHealing] 关键词未匹配到元素，回退到全量元素给 AI 分析")
            dom_summary = self._build_dom_summary(elements, keywords=None)
            if not dom_summary:
                _log.warning("[SelfHealing] 全量元素也为空，放弃 AI 兜底")
                self._record_failed_patch(target_hint, keyword)
                return None

        # ── 第一轮：校验模型 ──
        new_hint = self._call_ai_for_locator(
            keyword, dom_summary, model_type="verification"
        )
        if new_hint:
            _log.info("[SelfHealing] ✓ AI(校验模型) 命中: %s → %s", keyword, new_hint)
            return {"new_hint": new_hint}

        # ── 第二轮：任务模型（升级），附带截图 ──
        _log.info("[SelfHealing] 校验模型未命中，升级到任务模型+截图")
        screenshot_b64 = self._take_screenshot_base64(engine)
        new_hint = self._call_ai_for_locator(
            keyword, dom_summary, model_type="general", screenshot_b64=screenshot_b64
        )
        if new_hint:
            _log.info(
                "[SelfHealing] ✓ AI(任务模型+截图) 命中: %s → %s", keyword, new_hint
            )
            return {"new_hint": new_hint}

        # ── 兜底失败：回写 verified=False 补丁 ──
        _log.warning("[SelfHealing] ✗ AI 兜底策略失败: hint='%s'", target_hint)
        self._record_failed_patch(target_hint, keyword)
        return None
