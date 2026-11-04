# -*- coding: utf-8 -*-
from __future__ import annotations
"""
统一 Action 契约（双执行路径契约统一）
====================================
背景：Step3 用例生成与 Step4 执行存在两条执行路径——
  - 引导路径 _guided._exec_one_step（explorer_mixins/_action.py）：按 action 名逐个 if/elif 分支
  - 结构化路径 action_dispatchers → browser_tools（DB 是 action→tool 映射事实源）

历史漂移：结构化路径支持 drag / wheel / multi_touch / download_file，
而引导路径只有 19 个分支；引导路径对未知 action 仅 `_log.warning("[Guided] 未知action")`，
既不记证据也不判失败 → 模板生成但在引导路径无法执行的 action 会被"静默略过"，形成假通过。

本模块提供单一事实源（唯一契约），供生成层与两条执行层共用：
- CANONICAL_ACTIONS  生成层允许产出的全部 action 白名单
- GUIDED_IMPLEMENTED 引导路径已实现的分支集合
- is_canonical / classify_action / guided_gap  三态分类，用于在运行时暴露漂移而非静默忽略
"""

# 生成层（Step3 模板）允许产出的 step 原子交互 action 白名单（跨系统通用、无业务语义）。
# 只登记"真正会作为 step.action 产出并由双执行路径以分支实现"的原子动作。
# 模板名（如 tab_traverse）与流程级/证据级动作（如 login/logout/screenshot）不属于这里。
CANONICAL_ACTIONS: frozenset[str] = frozenset({
    # 基础交互
    "click", "double_click", "right_click", "hover",
    "fill", "type_text", "clear",
    "select_option", "check", "uncheck",
    "set_date", "upload_file",
    # 键盘
    "press_key", "key_down", "key_up",
    # 导航 / 等待 / 校验
    "navigate", "wait", "verify", "focus_check",
    # 滚动 / 拖拽 / 触控 / 下载
    "scroll", "scroll_wheel", "wheel", "drag", "drag_offset",
    "download_file", "download",
    "multi_touch", "multi_touch_pinch",
})

# 模板级复合动作名：由 apply_action_templates 展开成原子 action，
# 自身不作为 step.action 进入执行层，故不属于 CANONICAL_ACTIONS。
TEMPLATE_BUNDLES: frozenset[str] = frozenset({
    "tab_traverse",
})

# 流程级/语义级保留动作：由流程层、会话层或其它 harness 消费，
# 不是 Step4 单 step 原子的交互动作，故不属于 CANONICAL_ACTIONS。
#
# ★ 契约补全(2026-09-27)：screenshot 属证据级动作，本模块 docstring 早已把它
#   列为该类动作的示例，却漏登记 → classify_action 返回 "UNKNOWN" →
#   生成端"指令契约"守卫（_check_guard.sanitize_case_actions）把它当契约外动作
#   剔除。实测存量库 1085 条 screenshot 动作（占比最高的非交互动作）全部误剔。
#   证据（该类动作确实被消费，不是死词）：
#     - browser_tools/_tool_definitions.py:_TOOL_PREFIX_MAP
#       "browser_take_screenshot" → "screenshot"（工具→action 名映射）
#     - browser_tools/execution/_single.py 注册 _browser_take_screenshot 实现
#     - browser_structured_executor.py 的 _is_interactive 集合显式含 "screenshot"
#   通用规则：契约必须覆盖**已被实现且被消费**的全部 action 名；
#   只登记"会被 step.action 产出并由双执行路径以分支实现"的原子动作，
#   证据级/流程级动作登记于此，二者共同构成完整白名单。
RESERVED_ACTIONS: frozenset[str] = frozenset({
    "login", "logout", "submit",
    "screenshot",
})

# 引导路径 _exec_one_step 已实现的分支集合
GUIDED_IMPLEMENTED: frozenset[str] = frozenset({
    "click", "double_click", "right_click", "hover",
    "fill", "type_text", "clear",
    "select_option", "check", "uncheck",
    "set_date", "upload_file",
    "press_key", "key_down", "key_up",
    "navigate", "wait", "verify", "focus_check",
    "scroll", "scroll_wheel", "wheel", "drag", "drag_offset",
    "download_file", "download",
    "multi_touch", "multi_touch_pinch",
})


def is_canonical(action_type: str) -> bool:
    """action 是否在统一契约白名单内（生成层可产出 / 执行层可识别）。"""
    return (action_type or "").strip() in CANONICAL_ACTIONS


def guided_gap(action_type: str) -> bool:
    """引导路径未实现但契约认可（结构化已支持）的已登记 action → 潜在漂移。"""
    return (action_type or "").strip() in CANONICAL_ACTIONS and (
        action_type or ""
    ).strip() not in GUIDED_IMPLEMENTED


def classify_action(action_type: str) -> str:
    """动作分类（用于审阅与运行时暴露漂移）：
    - "GUIDED"    引导路径已实现，正常执行
    - "GAP"       契约已登记但引导路径未实现（结构化已有）→ 漂移，需告警
    - "TEMPLATE"  模板级复合动作名（由模板展开，非 step 原子 action）
    - "RESERVED"  流程级/语义级保留动作（由流程层/会话层消费）
    - "UNKNOWN"   契约外 action → 生成层/配置错误，需告警
    """
    atype = (action_type or "").strip()
    if atype in GUIDED_IMPLEMENTED:
        return "GUIDED"
    if atype in CANONICAL_ACTIONS:
        return "GAP"
    if atype in TEMPLATE_BUNDLES:
        return "TEMPLATE"
    if atype in RESERVED_ACTIONS:
        return "RESERVED"
    return "UNKNOWN"