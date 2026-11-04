# -*- coding: utf-8 -*-
"""单意图执行 — 结构化动作/断言执行 mixin。

职责：
- _execute_structured_actions: 执行结构化动作（click/fill/select/navigate/upload）
- _execute_structured_assertions: 执行结构化断言（assert/verify）

这些方法在 _base.py 的步骤循环分支中被调用。
"""

import logging

_log = logging.getLogger(__name__)


class _BrowserCoreSingleStructuredMixin:
    """结构化动作与断言执行能力。"""

    async def _execute_structured_actions(self, step, step_desc):
        """执行结构化动作（click/fill/select_option/navigate/upload）。

        委托到 BrowserCore 已有的结构化执行方法。
        若子类/其他 mixin 已提供此方法，本 mixin 仅为占位/文档用途。
        """
        # 实际实现由 BrowserCore 主类或其他 mixin 提供
        # 这里保持与原始逻辑一致：若未覆盖则抛出，确保调用链正确
        raise NotImplementedError(
            "_execute_structured_actions 应由 BrowserCore 主类或其他 mixin 实现"
        )

    async def _execute_structured_assertions(self, step, step_desc):
        """执行结构化断言（assert/verify）。

        委托到 BrowserCore 已有的结构化断言方法。
        若子类/其他 mixin 已提供此方法，本 mixin 仅为占位/文档用途。
        """
        raise NotImplementedError(
            "_execute_structured_assertions 应由 BrowserCore 主类或其他 mixin 实现"
        )

    def _resolve_step_hints_from_cache(
        self, step: dict, page_explorer, current_url: str
    ) -> None:
        """用页面探索器缓存解析 step 中所有 action/assertion 的 target_hint。

        将解析结果写入 action["_resolved_selector"] / action["_resolved_url"]。
        后续 browser_structured_executor 检测到 _resolved_selector 时直接使用，跳过 self-healing。
        """
        if not page_explorer:
            return
        for action in step.get("actions", []) + step.get("assertions", []):
            hint = action.get("target_hint", "")
            if not hint:
                continue
            resolved = page_explorer.resolve_hint(hint, current_url=current_url)
            if resolved and resolved.get("element"):
                selector = resolved["element"].get("selector", "")
                if selector:
                    action["_resolved_selector"] = selector
                    action["_resolved_score"] = resolved.get("score", 0)
                    _log.debug(
                        "[Cache] 解析 target_hint '%s' → selector '%s' (score=%.2f)",
                        hint[:40],
                        selector[:60],
                        resolved.get("score", 0),
                    )
                if resolved.get("url"):
                    action["_resolved_url"] = resolved["url"]
