# -*- coding: utf-8 -*-
from __future__ import annotations

"""
semantic_dom_tree_mixins._prompt — AI prompt 输出 mixin
（regions_to_prompt_text / _generate_ai_hint_impl / generate_ai_hint）
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class _SemanticDomPromptMixin:
    """提供 regions_to_prompt_text / _generate_ai_hint_impl / generate_ai_hint 方法。"""

    # ------------------------------------------------------------------
    # 转成给 AI 的 prompt 字符串
    # ------------------------------------------------------------------
    def regions_to_prompt_text(
        self,
        region_tree: dict[str, Any],
        max_regions: int = 5,
        include_elements: bool = True,
    ) -> str:
        """把区域树序列化为 AI 友好的文本。"""
        try:
            if not region_tree or not region_tree.get("regions"):
                return "[DOM 区域] 页面为空或解析失败。"

            total_regions = int(region_tree.get("total_regions") or 0)
            total_elems = int(region_tree.get("total_elements") or 0)
            regions = region_tree.get("regions") or []
            shown = regions[: max(1, int(max_regions))]
            truncated = len(regions) > len(shown)

            lines: list[str] = []
            lines.append(
                f"# DOM 语义区域摘要（共 {total_regions} 个区域，{total_elems} 个元素）"
            )
            lines.append(f"- 展示区域数: {len(shown)} / {total_regions}")
            if goal := region_tree.get("goal_text"):
                lines.append(f"- 用户目标: {goal}")
            lines.append("")

            for r in shown:
                lines.append(
                    f"## [{r.get('region_id', '')}] {r.get('name', '')}（{r.get('element_count', 0)} 个元素）"
                )
                lines.append(f"- 摘要: {r.get('summary', '')}")
                if r.get("top_keywords"):
                    lines.append(f"- 关键词: {' / '.join(r['top_keywords'])}")
                if include_elements and r.get("elements"):
                    lines.append("- 关键元素:")
                    for el in r["elements"][:12]:  # 每个区域最多 12 行
                        lines.append(
                            f"  * [{el.get('type', 'other')}:{el.get('tag', 'el')}] "
                            f"{el.get('text', '')}"
                        )
                lines.append("")

            if truncated:
                lines.append("⚠️ 未展开的区域可能包含目标元素，可根据页面滚动或展开查看")

            return "\n".join(lines).strip()
        except Exception as e:
            _log.warning("SemanticDomTree.regions_to_prompt_text 异常 %s", e)
            return "[DOM 区域] 序列化失败，请交给 AI 做兜底理解。"

    # ------------------------------------------------------------------
    # 给 AI 一个行动提示（看哪个区域）
    # ------------------------------------------------------------------
    def _generate_ai_hint_impl(
        self, goal_text: str, region_tree: dict[str, Any]
    ) -> str:
        try:
            if not region_tree or not region_tree.get("regions"):
                return "页面为空或解析失败，建议先刷新或检查 DOM。"

            goal = (goal_text or "").lower()
            regions = region_tree["regions"]

            # 按目标关键词与区域关键词匹配度做加权评分
            scored: list[tuple[float, dict[str, Any]]] = []
            for r in regions:
                try:
                    score = 0.0
                    kws = [k.lower() for k in (r.get("top_keywords") or [])]
                    name = (r.get("name") or "").lower()
                    # 目标与区域关键词匹配
                    if goal:
                        for kw in kws:
                            if kw and kw in goal:
                                score += 1.0
                    # 目标与区域名匹配（权重更高）
                    if goal and name:
                        for token in goal.split():
                            if token and token in name:
                                score += 1.2
                    # 根据目标类型（如登录/删除）对区域类型做偏好加权
                    if any(k in goal for k in ("登录", "login", "密码", "账号")):
                        if "数据填写" in name or "表单" in name or "输入" in name:
                            score += 1.5
                    if any(k in goal for k in ("删除", "delete", "remove", "移除")):
                        if "列表" in name or "数据" in name or "表格" in name:
                            score += 1.5
                    if any(
                        k in goal for k in ("查询", "搜索", "筛选", "search", "query")
                    ):
                        if "查询" in name or "表单" in name:
                            score += 1.5
                    if any(
                        k in goal
                        for k in ("新增", "创建", "添加", "新建", "create", "add")
                    ):
                        if "操作" in name or "管理" in name or "按钮" in name:
                            score += 1.2
                    if any(
                        k in goal for k in ("导航", "跳转", "菜单", "navigate", "menu")
                    ):
                        if "导航" in name:
                            score += 1.5
                    scored.append((score, r))
                except Exception as _e:
                    _log.debug("[prompt] 非关键异常: %s", _e, exc_info=True)
                    continue

            if not scored:
                return "无法识别目标区域，建议从上到下依次理解各区域。"

            # 按得分降序
            scored.sort(key=lambda x: -x[0])
            top_score, top_region = scored[0]

            if top_score <= 0:
                # 没有明显命中：建议先看第 0 区域（顶部一般是导航/搜索）
                first = regions[0]
                return (
                    f"当前目标无明显匹配区域，建议先检查页面最上方的「{first.get('name')}」"
                    f"（{first.get('element_count', 0)} 个元素）。"
                )

            return (
                f"建议优先查看「{top_region.get('name')}」"
                f"（region_id={top_region.get('region_id')}, "
                f"{top_region.get('element_count', 0)} 个元素, "
                f"关键词: {', '.join((top_region.get('top_keywords') or [])[:4]) or '无'}）。"
            )
        except Exception as e:
            _log.warning("SemanticDomTree._generate_ai_hint_impl 异常 %s", e)
            return "区域解析失败，请以原始 DOM 元素列表兜底理解。"

    def generate_ai_hint(
        self, goal_text: str, region_tree: dict[str, Any] | None = None
    ) -> str:
        """对外暴露的 hint 生成函数；若未传 tree，则复用上次的 tree 或返回默认字符串。"""
        try:
            tree = region_tree if region_tree else self._last_tree
            if tree is None:
                self._last_hint = "尚未构建区域树，建议先调用 build_region_tree。"
                return self._last_hint
            hint = self._generate_ai_hint_impl(goal_text, tree)
            self._last_hint = hint
            return hint
        except Exception as e:
            _log.warning("SemanticDomTree.generate_ai_hint 异常 %s", e)
            return "区域提示生成失败。"
