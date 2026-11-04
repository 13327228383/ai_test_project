# -*- coding: utf-8 -*-
from __future__ import annotations

"""
规则表与匹配逻辑
"""

import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 规则表 (按优先级从上到下): (limit, reason_cn, keywords_cn, keywords_en)
# ---------------------------------------------------------------------------
# 规则表: (limit, reason_cn, keywords_cn, keywords_en, weight_bonus)
# weight_bonus: 该规则的权重加成，用于解决歧义（如同时含"审批"和"配置"时，
# 配置规则给更高权重，因为配置更可能涉及大表单）。
_RULES: list[tuple[int, str, list[str], list[str], float]] = [
    (
        30,
        "极简场景 — 仅导航或单一按钮点击",
        ["导航", "跳转", "进入页面", "打开页面", "点击菜单"],
        ["navigate", "nav", "jump", "go to page", "open page"],
        0.0,
    ),
    (
        40,
        "小表单场景 — 登录/注册，元素极少",
        ["登录", "登陆", "注册账号", "登出系统", "退出登录", "signin", "signup"],
        ["login", "log in", "sign in", "sign up", "logout", "log out"],
        0.0,
    ),
    (
        40,
        "分页控制 — 页面元素相对固定",
        [
            "分页",
            "翻页",
            "翻到第",
            "跳到第",
            "下一页",
            "上一页",
            "第1页",
            "第 1 页",
            "页码",
            "每页",
        ],
        ["page", "next page", "prev page", "pagination", "paging", "go to page"],
        0.0,
    ),
    (
        50,
        "删除操作 — 定位目标行 + 确认弹窗",
        ["删除", "移除", "删掉", "del"],
        ["delete", "remove", "destroy"],
        0.0,
    ),
    (
        50,
        "导出/下载 — 定位按钮 + 等待下载",
        ["导出", "下载", "下载文件", "导出表格", "导出数据", "export", "download"],
        ["export as", "download", "export to", "export data"],
        0.0,
    ),
    # 注: 普通"审批"保持低复杂度(60)，但下方的"复杂流程"权重更高，会覆盖它
    (
        60,
        "审批/审核/确认 — 对话框场景，元素不多",
        [
            "审批这个",
            "审核通过",
            "复核",
            "同意这个",
            "驳回这个",
            "点击同意",
            "点击批准",
        ],
        ["approve this", "approval dialog", "reject this", "confirm dialog"],
        0.0,
    ),
    (
        80,
        "搜索/筛选/查询 — 中等复杂度表单",
        ["搜索", "筛选", "查询", "查找", "检索", "过滤"],
        ["search", "filter", "query", "find"],
        0.0,
    ),
    (
        100,
        "表单填写/新增记录 — 表单+按钮，元素较多",
        [
            "填写表单",
            "录入",
            "新增记录",
            "创建记录",
            "添加记录",
            "新建记录",
            "更新记录",
            "编辑记录",
        ],
        ["fill form", "create record", "add new", "new record", "edit record"],
        0.0,
    ),
    # 高权重: 涉及"配置/参数/流程"的大表单场景，应该给更多 DOM 空间
    (
        150,
        "复杂业务流程 — 含流程/审批/配置关键字",
        [
            "工作流",
            "业务流程",
            "配置项",
            "参数设置",
            "系统设置",
            "策略配置",
            "审批流程",
        ],
        ["workflow", "flow", "process", "config", "configure", "setting", "policy"],
        1.0,
    ),
]

_DEFAULT_LIMIT = 120
_DEFAULT_REASON = "默认场景 — 未识别到明确意图，使用默认上限"


def _match_keywords(text: str, cns: list[str], ens: list[str]) -> bool:
    """判断文本是否命中任意关键词（简单包含匹配）。"""
    try:
        if not text:
            return False
        t = text.lower()
        for kw in cns:
            if kw and kw in t:
                return True
        for kw in ens:
            if kw and kw in t:
                return True
        return False
    except Exception as e:
        _log.warning("dynamic_dom_sizer._match_keywords 异常 %s", e)
        return False


def _extract_text(goal_text: str, intent_schema: dict[str, Any] | None = None) -> str:
    """把 goal_text + intent_schema 里的关键文本合并。v9: title / type / title。"""
    try:
        parts: list[str] = [goal_text or ""]
        if intent_schema and isinstance(intent_schema, dict):
            for key in ("title", "type", "title", "description"):
                v = intent_schema.get(key)
                if v:
                    parts.append(str(v))
        return " ".join(p for p in parts if p)
    except Exception as e:
        _log.warning("dynamic_dom_sizer._extract_text 异常 %s", e)
        return goal_text or ""


def _size_to_hint(limit: int) -> str:
    """根据 limit 大小映射到 compact/normal/full。"""
    try:
        if limit <= 50:
            return "compact"
        if limit <= 100:
            return "normal"
        return "full"
    except Exception as _e:
        _log.debug("[_rules] _size_to_hint 异常: %s", _e, exc_info=True)
        return "normal"


def _match_rule(intent_text: str) -> tuple[int | None, str | None, str | None]:
    """
    根据意图文本匹配规则，返回 (limit, reason, context_hint)。
    未命中时返回 (None, None, None)。
    """
    try:
        text = intent_text or ""
        if not text:
            return None, None, None

        best = None  # (score, limit, reason)
        for rule in _RULES:
            try:
                if len(rule) >= 5:
                    limit, reason, cns, ens, bonus = (
                        rule[0],
                        rule[1],
                        rule[2],
                        rule[3],
                        float(rule[4]),
                    )
                elif len(rule) == 4:
                    limit, reason, cns, ens = rule[0], rule[1], rule[2], rule[3]
                    bonus = 0.0
                else:
                    continue
                t_lower = text.lower()
                hit = 0
                for kw in list(cns) + list(ens):
                    if kw and kw.lower() in t_lower:
                        hit += 1
                if hit > 0:
                    score = float(hit) + bonus
                    if best is None or score > best[0]:
                        best = (score, limit, reason)
            except Exception as inner:
                _log.debug("dynamic_dom_sizer: 规则评分异常 %s", inner)
                continue

        if best is not None and best[0] > 0:
            score, limit, reason = best
            hint = _size_to_hint(limit)
            return limit, f"{reason} (score={score:.1f})", hint

        return None, None, None
    except Exception as e:
        _log.warning("dynamic_dom_sizer._match_rule 异常 %s", e)
        return None, None, None


def get_dom_limit(intent_text: str = "") -> int:
    """
    根据意图文本返回 DOM 元素上限。
    优先使用覆盖值（由 _override 模块提供），其次匹配规则表，最后回退默认值。
    """
    from ._override import _OVERRIDE_LIMIT
    from ._metric import _record_sizer_metric

    try:
        # 1) 自定义覆盖（最高优先级）
        if _OVERRIDE_LIMIT is not None:
            _record_sizer_metric(_OVERRIDE_LIMIT, "override", "override")
            return _OVERRIDE_LIMIT

        # 2) 合并文本并匹配
        text = intent_text or ""
        if not text:
            _record_sizer_metric(_DEFAULT_LIMIT, "empty_text", "normal")
            return _DEFAULT_LIMIT

        limit, reason, hint = _match_rule(text)
        if limit is not None:
            _record_sizer_metric(limit, text[:30], hint or "normal")
            _log.info("DynamicDomSizer: 命中「%s」→ limit=%d", reason, limit)
            return limit

        # 3) 未命中：默认值
        _record_sizer_metric(_DEFAULT_LIMIT, "fallback", "normal")
        _log.info("DynamicDomSizer: 未命中任何规则，返回默认上限 %d", _DEFAULT_LIMIT)
        return _DEFAULT_LIMIT
    except Exception as e:
        _log.warning("dynamic_dom_sizer.get_dom_limit 顶层异常 %s", e)
        return _DEFAULT_LIMIT


def get_context_size_hint(intent_text: str = "") -> str:
    """返回最近一次决策的三档提示。"""
    try:
        limit = get_dom_limit(intent_text)
        return _size_to_hint(limit)
    except Exception as _e:
        _log.debug("[_rules] get_context_size_hint 异常: %s", _e, exc_info=True)
        return "normal"
