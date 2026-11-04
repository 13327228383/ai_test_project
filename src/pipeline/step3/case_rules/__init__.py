# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""用例生成规则模块 — 规则优先，AI 兜底。

每个规则文件一个类，继承 BaseCaseRule。
注册表自动发现所有规则，按优先级排序。
后端 API 可查询所有规则清单。
"""

import logging
from typing import Any

from .base import BaseCaseRule

_log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 规则注册表（自动发现）
# ──────────────────────────────────────────────

_RULE_REGISTRY: list[BaseCaseRule] = []
_RULE_MAP: dict[str, BaseCaseRule] = {}  # rule_id → instance
_RULE_INITIALIZED = False


def _discover_rules() -> list[BaseCaseRule]:
    """自动发现 case_rules 目录下所有规则类并实例化。"""
    import importlib
    import pkgutil
    import inspect

    from . import base as _base_mod

    rules = []
    for _, modname, _ in pkgutil.iter_modules(__path__, prefix=__name__ + "."):
        if modname.endswith(".base"):
            continue
        try:
            mod = importlib.import_module(modname)
        except Exception:
            _log.exception("加载规则模块失败: %s", modname)
            continue

        for _name, obj in inspect.getmembers(mod, inspect.isclass):
            if obj is BaseCaseRule or not issubclass(obj, BaseCaseRule):
                continue
            if getattr(obj, "rule_id", ""):
                with guard("_discover_rules: instance = obj()", log=_log, level=logging.ERROR):
                    instance = obj()
                    rules.append(instance)
                    _log.debug("注册规则: %s (%s)", instance.rule_id, instance.name)

    rules.sort(key=lambda r: r.priority, reverse=True)
    return rules


def _ensure_rules():
    global _RULE_REGISTRY, _RULE_MAP, _RULE_INITIALIZED
    if _RULE_INITIALIZED:
        return
    _RULE_REGISTRY = _discover_rules()
    _RULE_MAP = {r.rule_id: r for r in _RULE_REGISTRY}
    _RULE_INITIALIZED = True
    _log.info("用例生成规则加载完成: %d 条", len(_RULE_REGISTRY))


# ──────────────────────────────────────────────
# 公共 API
# ──────────────────────────────────────────────


def get_all_rules() -> list[BaseCaseRule]:
    """获取所有规则（按优先级降序）。"""
    _ensure_rules()
    return list(_RULE_REGISTRY)


def get_rule_meta_list() -> list[dict[str, Any]]:
    """获取所有规则的元数据列表（供 API 返回）。"""
    _ensure_rules()
    return [r._meta() for r in _RULE_REGISTRY]


def match_rule(req: dict[str, Any], control_type: str) -> BaseCaseRule | None:
    """按需求匹配规则（优先级最高的最先匹配）。"""
    _ensure_rules()
    for rule in _RULE_REGISTRY:
        if rule.match(req, control_type):
            return rule
    return None


def generate_cases(
    req: dict[str, Any],
    target_page: str,
    control_type: str,
    merged_data: dict[str, Any],
    selected_ops: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """规则优先生成用例。匹配到规则 → 直接生成；未匹配 → 返回 None（走 AI 兜底）。

    ★ 通用修复(2026-07-20 v5): 统一注入 sidebar click
      原bug: case_rules 注册表规则生成 actions 后直接 return，绕过 _build_actions，
            导致 v4 前置匹配的 sidebar_text 无法注入到 actions 中。
      根因: case_rules 各规则的 generate 方法只调用 self._nav(target_page)，
            不知道 sidebar_text 的存在；v4 修复只在 _build_actions 内注入。
      修复: 在 generate_cases 入口统一注入 — 规则生成用例后，遍历每条用例的 actions，
            在第一个 navigate 之后插入 click(sidebar_text)（如果 req 含 _v4_sidebar_click_text）。
      通用性: 适用于所有 case_rules 规则（button/upload/input/select/search/...），
              不依赖具体规则实现，纯基于 req._v4_sidebar_click_text 元数据。
      合理性: 追根溯源到 case_rules 绕过 _build_actions 的架构缺陷，统一入口注入是治本方案。
      覆盖性: 覆盖所有 case_rules 规则生成的用例，与 _build_actions 的 v4 注入逻辑形成双保险。
    """
    rule = match_rule(req, control_type)
    if rule is None:
        _log.info("无匹配规则，control_type=%s，回退 AI 兜底", control_type)
        return None
    _log.info(
        "规则命中: %s (%s) → control_type=%s", rule.rule_id, rule.name, control_type
    )
    cases = rule.generate(req, target_page, control_type, merged_data, selected_ops)
    if not cases:
        return cases

    # ★ v5: 统一注入 sidebar click（覆盖所有 case_rules 规则）
    _v5_sidebar = str(req.get("_v4_sidebar_click_text", "") or "").strip()
    _v5_real_url = str(req.get("_v4_real_url", "") or "").strip()
    if _v5_sidebar:
        _injected = 0
        for _case in cases:
            _actions = _case.get("actions", [])
            if not isinstance(_actions, list) or not _actions:
                continue
            # 找到第一个 navigate action 的位置
            _nav_idx = -1
            for _i, _a in enumerate(_actions):
                if (
                    isinstance(_a, dict)
                    and str(_a.get("type", "")).lower() == "navigate"
                ):
                    _nav_idx = _i
                    break
            if _nav_idx < 0:
                continue
            # 检查是否已注入过 sidebar click（避免重复）
            _already = any(
                isinstance(_a, dict)
                and str(_a.get("type", "")).lower() == "click"
                and _a.get("target_hint") == _v5_sidebar
                for _a in _actions
            )
            if _already:
                continue
            # ★ 方案B (ADR-017, 2026-07-23): real_url 已知时 navigate 直达，跳过 sidebar click
            #   根因(§10.9 3层追问):
            #     1. 表层: sidebar_text "辅材:批量导入辅材" 在DOM按钮列表中不存在 → 返回 None（降级=失败）
            #     2. 中层: sidebar_text 来自 nav_map 菜单项文本，不是目标页面DOM按钮 → _control_validation 误判缺失
            #     3. 根因: v5 sidebar 注入在 real_url 已知时仍插入 click(sidebar_text)，与 navigate(real_url) 冗余
            #   通用性(§12.4 9维度):
            #     1. 跨系统: real_url 直达适用于所有 SPA 系统
            #     2. 跨行业: 语义意图适用于任何行业
            #     3. 跨页面: 列表/详情/表单/Tab 页都适用
            #     4. 跨Tab: real_url 含 tab 参数，直达目标 Tab
            #     5. 数据完整性: real_url 缺失时降级为 intent: 语义意图
            #     6. 根因溯源: 修架构缺陷（冗余注入），非打补丁
            #     7. 覆盖性: 影响所有 case_rules 规则（统一入口注入）
            #     8. 全局观: 消除第6个降级路径，与 Plan B 一致
            #     9. 职责边界: Step3 生成 intent:，Step4 实时感知执行
            #   自我反驳三次:
            #     1) 跳过 sidebar click 会丢失导航？→ 不会。real_url 已知时 navigate 直达目标页面
            #     2) real_url 未知时不注入？→ 否。用 intent: 前缀，Step4 实时找 sidebar 菜单项
            #     3) 删掉 v5 注入？→ 否。real_url 未知时仍需 sidebar 导航
            if _v5_real_url:
                _nav_action = _actions[_nav_idx]
                _nav_action["target_hint"] = _v5_real_url
                _nav_action["url"] = _v5_real_url
                # real_url 已知，navigate 直达目标页面，跳过 sidebar click
                continue
            # ★ P0-fix(2026-07-24): 回滚 ADR-017 intent: 前缀 — 用 sidebar 原始文本
            #   原: intent:{sidebar} 让 Step4 实时找 → 违反 §12.1 Step4 只执行不决策
            #   修复: 用 sidebar 原始文本，Step4 常规 click 找，找不到诚实报错
            #   通用性: 适用于所有 real_url 未知的 sidebar 导航场景
            _actions.insert(_nav_idx + 1, {"type": "click", "target_hint": _v5_sidebar})
            _injected += 1
        if _injected > 0:
            _log.info(
                "[case_rules] ★ v5 统一注入 sidebar click: 规则=%s 注入 %d 条用例, sidebar='%s'",
                rule.rule_id,
                _injected,
                _v5_sidebar,
            )

    # ★ P0-fix(2026-07-24): 回滚 ADR-017 方案B intent: 替换 — 恢复 navigate 原始 target_hint/url
    #   原 ADR-017 逻辑: 统一替换 navigate target_hint 为 intent: 语义前缀，删除 url
    #   回滚原因(§10.9 根因核查):
    #     1. 现象: Step4 navigate 用 intent: 语义 → Step4 需"理解语义找菜单" → 决策 → 违反 §12.1
    #     2. 直接原因: ADR-017 删除 url，让 Step4 走菜单导航（Step4 做导航决策）
    #     3. 中间原因: Step4 菜单导航依赖 AI 循环 → 找不到菜单 → 超时
    #     4. 根本原因: ADR-017 让 Step4 做 Step1/Step3 的导航决策，违反职责边界
    #   全局观: 与 L1989-2019 的降级修复形成闭环 — Step3 用真实 url，Step4 只执行导航
    #   职责边界: navigate 的 target_hint/url 是 Step3 的职责(§13.3)，Step4 只执行(§12.1)
    #   通用性: 适用于所有 case_rules 规则，保留原始 nav_map 数据
    #   自我反驳三次:
    #     1. 保留intent:? → Step4做决策，违反§12.1
    #     2. 只删intent:不恢复url? → Step4无url无法导航
    #     3. 完全回滚保留原始target_hint/url? → 符合§13.3，Step4只执行，最优
    #   实施: 删除 intent: 替换逻辑，保留 case_rules 规则生成的原始 navigate

    return cases
