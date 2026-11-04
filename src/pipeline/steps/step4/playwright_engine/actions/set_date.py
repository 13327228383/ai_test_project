# -*- coding: utf-8 -*-
"""日期选择器动作 — set_date_element。

用于某些不支持 fill 的 DatePicker 组件（如原生 input[type=date]、
日历弹窗组件）。尝试多种策略：原生 setValue → JS 赋值 → 逐字输入。
2 级兜底链：ai_id 选择器 → 语义选择器。
"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


class SetDateMixin:
    """日期选择器动作。"""

    async def set_date_element(
        self,
        ai_id: str,
        value: str,
        target_hint: str = "",
        fallback_selectors: list = None,
    ) -> bool:
        """设置日期值。

        value 格式: 'YYYY-MM-DD'（标准）或组件所需格式。
        多级策略：原生 fill → JS 赋值+事件 → 逐字输入。
        2 级兜底链：ai_id 选择器 → 语义选择器。
        """

        async def _do(el) -> bool:
            try:
                await el.scroll_into_view_if_needed()
            except Exception as _se:
                _log.debug("[set_date] scroll_into_view 失败: %s", _se, exc_info=True)
            # 策略1: 原生 fill（适用于 input[type=date]）
            with guard("_do: await el.fill(value, timeout=3000)", log=_log):
                await el.fill(value, timeout=3000)
                return True
            # 策略2: JS 赋值 + 触发事件
            with guard("_do: await el.evaluate( '(el, val) => {' ", log=_log):
                await el.evaluate(
                    "(el, val) => {"
                    "  el.value = val;"
                    "  el.dispatchEvent(new Event('input', {bubbles:true}));"
                    "  el.dispatchEvent(new Event('change', {bubbles:true}));"
                    "  el.dispatchEvent(new Event('blur', {bubbles:true}));"
                    "}",
                    value,
                )
                return True
            # 策略3: 点击 + 逐字输入
            try:
                await el.click(timeout=3000)
                await el.type(value, delay=50, timeout=5000)
                return True
            except Exception as e:
                _log.debug("[set_date] 逐字输入失败: %s", e)
                return False

        return await self._locate_and_act(
            ai_id, target_hint, fallback_selectors, _do, "set_date"
        )
