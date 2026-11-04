# -*- coding: utf-8 -*-
"""页面装载期 JS 报错归属（单一事实源）。

语义契约（GAP-01 的唯一判据来源）：
  - 「装载期」= 主框架发生导航 → `load` 事件后的短暂静默窗口；
    SPA 软路由不触发 `load` 时，以 ``NAV_PHASE_CAP_SEC`` 为硬上界。
  - 装载期内产生的 ``console.error`` / ``pageerror`` 是**页面级事实**：
    同一页面对任何用例都成立，与用例动作无关。不同浏览器实现对同一份页面缺陷的
    上报时机不同——部分实现只在真正执行内联处理器时报 1 条，部分实现在文档解析
    完成时就把页面上每个内联处理器各报一条。若按用例累计，一份页面缺陷会被放大成
    N 份用例 FAIL（实测 5 条报错把 60+ 个自身需求已通过的用例判成假 Bug）。
  - 只有页面进入稳定态之后产生的 JS 报错，才是**当前用例的动作**引发的，才归因到
    该用例（`_base._execute_one_intent` 的 GAP-01 消费点）。

通用性：判据只依赖 W3C/Playwright 的导航与 load 事件语义，跨浏览器实现、跨系统、
跨行业、跨页面成立，不含任何业务词表或浏览器版本判断。
"""

from __future__ import annotations

import time

# 软路由（同文档导航）无 load 事件时的装载期上界（秒）
NAV_PHASE_CAP_SEC: float = 2.0
# load 事件之后的静默窗口（秒）：装载收尾阶段（资源解析尾音）仍属页面级。
# 取小值：只覆盖 load 之后的收尾噪音，避免把用例紧跟 load 的交互报错也吞成页面级。
LOAD_QUIET_SEC: float = 0.1

# 证据条目上的归属标记键（生产者写入，消费者只读）
PAGE_LOAD_KEY = "page_load"


class PageLoadPhaseTracker:
    """跟踪「当前是否处于页面装载期」，供 console/pageerror 生产者打归属标记。"""

    def __init__(self) -> None:
        self._until: float = 0.0

    def mark_navigated(self) -> None:
        """主框架开始导航：打开装载期（以硬上界收口，等 load 事件精确收口）。"""
        self._until = time.monotonic() + NAV_PHASE_CAP_SEC

    def mark_loaded(self) -> None:
        """load 事件：装载期在短暂静默后收口。"""
        self._until = max(self._until, time.monotonic() + LOAD_QUIET_SEC)

    def is_load_phase(self) -> bool:
        return time.monotonic() < self._until


def is_case_attributable(entry) -> bool:
    """该条 JS 报错是否应归因到当前用例（False = 页面级装载期事实）。

    消费者（GAP-01）唯一判据，禁止各处自行按文案过滤。
    """
    return not (isinstance(entry, dict) and entry.get(PAGE_LOAD_KEY))
