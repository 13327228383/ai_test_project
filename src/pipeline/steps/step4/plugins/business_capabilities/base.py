# -*- coding: utf-8 -*-
from __future__ import annotations

"""
_base.py — 模块级常量 + 辅助函数 + KPI 埋点
=============================================
作为 _BusinessCapBaseMixin 提供给 BusinessCapabilities 多继承体系。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class _BusinessCapBaseMixin:
    """基础 mixin：常量、元素辅助函数、指标埋点。"""

    # -------------------------------------------------------------------
    # 模块级常量（常见按钮 / 输入框 / 表格 / 链接等关键词）
    # -------------------------------------------------------------------
    _BUTTON_KWS = [
        "按钮",
        "button",
        "btn",
        "submit",
        "登录",
        "login",
        "新增",
        "创建",
        "添加",
        "新建",
        "删除",
        "delete",
        "导出",
        "export",
        "下载",
        "download",
        "查询",
        "搜索",
        "search",
        "filter",
        "筛选",
        "保存",
        "save",
        "提交",
        "确认",
        "confirm",
        "同意",
        "approve",
        "驳回",
        "reject",
        "批量",
        "batch",
        "next",
        "上一页",
        "下一页",
        "page",
    ]

    _INPUT_KWS = [
        "用户名",
        "password",
        "密码",
        "账号",
        "account",
        "email",
        "邮箱",
        "phone",
        "电话",
        "input",
        "输入框",
        "输入",
        "textarea",
        "search",
        "查询",
        "keyword",
        "关键词",
    ]

    _TABLE_KWS = ["table", "表格", "列表", "grid", "datagrid", "数据行", "row"]

    _LINK_KWS = [
        "a",
        "anchor",
        "href",
        "link",
        "导航",
        "菜单",
        "menu",
        "首页",
        "home",
        "数据",
        "dashboard",
        "管理",
    ]

    # -------------------------------------------------------------------
    # 辅助工具：从 compact_state 中统计常见元素类别
    # -------------------------------------------------------------------
    @staticmethod
    def _element_text(el: Any) -> str:
        """从一个 compact_state 元素里提取可读文本（容错处理各种字段名）。"""
        try:
            if el is None:
                return ""
            if isinstance(el, str):
                return el
            if isinstance(el, dict):
                for key in (
                    "text",
                    "label",
                    "placeholder",
                    "aria_label",
                    "aria-label",
                    "name",
                    "value",
                    "title",
                    "tag",
                ):
                    v = el.get(key)
                    if v:
                        return str(v)
            # 若是对象，取 __str__ 前 60 字
            return str(el)[:60]
        except Exception as e:
            _log.warning("business_capabilities: _element_text 异常 %s", e)
            return ""

    @staticmethod
    def _element_tag(el: Any) -> str:
        """提取 DOM 元素的 tag / 类型字段。"""
        try:
            if isinstance(el, dict):
                t = el.get("tag") or el.get("type") or el.get("role") or ""
                return str(t).lower()
            return ""
        except Exception as _e:
            _log.debug("[base] _element_tag 异常: %s", _e, exc_info=True)
            return ""

    @classmethod
    def _count_by_keyword(cls, elements: list[Any], keywords: list[str]) -> int:
        """统计 text / tag 中包含任一关键词的元素数量。"""
        try:
            cnt = 0
            kws_lower = [k.lower() for k in keywords]
            for el in elements:
                text = (cls._element_text(el) + " " + cls._element_tag(el)).lower()
                if any(k in text for k in kws_lower):
                    cnt += 1
            return cnt
        except Exception as e:
            _log.warning("business_capabilities: _count_by_keyword 异常 %s", e)
            return 0

    @classmethod
    def _has_input(cls, elements: list[Any]) -> bool:
        try:
            for el in elements:
                tag = cls._element_tag(el)
                if tag in ("input", "textarea", "select", "password", "text"):
                    return True
                text = cls._element_text(el).lower()
                if any(
                    k in text
                    for k in [
                        "用户名",
                        "password",
                        "密码",
                        "账号",
                        "邮箱",
                        "输入框",
                        "input",
                    ]
                ):
                    return True
            return False
        except Exception as _e:
            _log.debug("[base] _has_input 异常: %s", _e, exc_info=True)
            return False

    @classmethod
    def _has_button(
        cls, elements: list[Any], keywords: list[str] | None = None
    ) -> bool:
        try:
            for el in elements:
                tag = cls._element_tag(el)
                text = cls._element_text(el).lower()
                is_button_like = (
                    tag in ("button", "submit", "a") or "btn" in tag or "按钮" in text
                )
                if is_button_like:
                    if keywords:
                        if any(k.lower() in text for k in keywords):
                            return True
                    else:
                        return True
            return False
        except Exception as _e:
            _log.debug("[base] _has_button 异常: %s", _e, exc_info=True)
            return False

    @classmethod
    def _has_table(cls, elements: list[Any]) -> bool:
        try:
            for el in elements:
                tag = cls._element_tag(el)
                text = cls._element_text(el).lower()
                if tag in ("table", "grid") or any(k in text for k in cls._TABLE_KWS):
                    return True
            return False
        except Exception as _e:
            _log.debug("[base] _has_table 异常: %s", _e, exc_info=True)
            return False

    # -------------------------------------------------------------------
    # KPI 埋点：调用 metrics_collector 记录能力命中/缺失
    # -------------------------------------------------------------------
    @staticmethod
    def _record_capability_metric(
        hint: str, mode: str, score: float, case_preview: str
    ):
        try:
            from src.backend.state import _thread_run_id as _tid
            from src.core.config_loader import config as _cfg
            from src.extensions.metrics_collector import metrics_collector as _mc

            rid = getattr(_tid, "value", "") or ""
            if rid and _cfg and getattr(_cfg, "project_name", ""):
                _mc.record_capability_hit(
                    _cfg.project_name,
                    rid,
                    hint,
                    mode=mode,
                    score=score,
                    case_preview=case_preview,
                )
        except Exception as _e:
            _log.debug(
                "[business_capabilities] 埋点失败(不影响主流程): %s", _e, exc_info=True
            )

    @staticmethod
    def _record_capability_miss(reason: str = ""):
        try:
            from src.backend.state import _thread_run_id as _tid
            from src.core.config_loader import config as _cfg
            from src.extensions.metrics_collector import metrics_collector as _mc

            rid = getattr(_tid, "value", "") or ""
            if rid and _cfg and getattr(_cfg, "project_name", ""):
                _mc.record_capability_miss(_cfg.project_name, rid, reason=reason)
        except Exception as _e:
            _log.debug("[base] _record_capability_miss 异常: %s", _e, exc_info=True)
