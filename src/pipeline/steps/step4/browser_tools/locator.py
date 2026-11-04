# -*- coding: utf-8 -*-
"""Playwright 元素定位器（门面模块）— 从原 locator.py 拆分（§4.3 单文件≤800行）。

★ 架构债清理（2026-07-25）: 按功能拆分为6个子模块，本文件作为门面 re-export 公共 API。
  - _hint_utils.py: hint工具（_normalize_hint/_extract_core_keyword/_build_*_selector）
  - _fuzzy_selectors.py: 模糊选择器常量
  - _basic_locator.py: 基础定位+modal（_locate_by_hint/_locate_with_fuzzy_fallback/_find_visible_modal/_locate_in_modal）
  - _intent_data.py: 意图定位数据层（域关键词+rag/nav_map查询）
  - _intent_executor.py: 意图定位执行层（导航+扫描按钮）
  - _fallback_locator.py: 统一fallback（_locate_with_fallback）

向后兼容：历史代码 `from locator import _locate_with_fallback` 等仍可用。
"""
import logging

# ★ re-export 所有公共函数（向后兼容）
from src.pipeline.steps.step4.browser_tools._hint_utils import (  # noqa: F401
    _HINT_PREFIXES,
    _normalize_hint,
    _TYPE_SUFFIXES,
    _CORE_ACTION_WORDS,
    _extract_core_keyword,
    _build_text_selector,
    _build_placeholder_selector,
    _build_label_selector,
    _build_id_selector,
    _build_name_selector,
    _build_ai_id_selector,
    _build_role_selector,
    _build_css_selector,
    _build_tag_selector,
    _PREFIX_HANDLERS,
)
from src.pipeline.steps.step4.browser_tools._fuzzy_selectors import (  # noqa: F401
    _CLICK_FUZZY_SELECTORS,
    _INPUT_FUZZY_SELECTORS,
)
from src.pipeline.steps.step4.browser_tools._basic_locator import (  # noqa: F401
    _locate_by_hint,
    _locate_with_fuzzy_fallback,
    _find_visible_modal,
    _locate_in_modal,
)
from src.pipeline.steps.step4.browser_tools._intent_data import (  # noqa: F401
    _extract_domain_keywords,
    _find_button_page_in_rag,
    _load_nav_map_pages,
    _load_rag_pages,
)
from src.pipeline.steps.step4.browser_tools._intent_executor import (  # noqa: F401
    _navigate_to_page_and_find_button,
    _find_button_via_nav_map,
    _find_button_via_tab_exploration,
    _locate_by_intent,
)
from src.pipeline.steps.step4.browser_tools._fallback_locator import (  # noqa: F401
    _locate_with_fallback,
)

_log = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# 前置验证：交互动作执行前检查元素状态（存在性 + 可见性 + enabled）
# -----------------------------------------------------------------------------
class PreVerifyResult:
    """前置验证结果。"""

    def __init__(self, passed: bool, message: str = ""):
        self.passed = passed
        self.message = message


async def pre_verify_interactive_element(locator) -> "PreVerifyResult":
    """交互动作执行前检查元素状态。

    Args:
        locator: Playwright Locator 实例

    Returns:
        PreVerifyResult: passed=True 可交互；passed=False 不可交互，message 含原因。
    """
    try:
        if await locator.count() == 0:
            return PreVerifyResult(False, "元素不存在（DOM 中未找到）")
        if not await locator.is_visible():
            return PreVerifyResult(
                False, "元素不可见（display:none/visibility:hidden 或移出视口）"
            )
        if not await locator.is_enabled():
            return PreVerifyResult(False, "元素已禁用（disabled 属性存在）")
        return PreVerifyResult(True, "")
    except Exception as e:
        _log.debug("[locator] pre_verify_interactive_element 异常: %s", str(e)[:80])
        return PreVerifyResult(True, f"验证异常，跳过: {str(e)[:60]}")
