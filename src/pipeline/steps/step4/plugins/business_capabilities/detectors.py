# -*- coding: utf-8 -*-
"""
_detectors.py — 12 个能力各自的 detector（规则实现，不调用 AI）
=============================================================
作为 _BusinessCapDetectorsMixin 提供给 BusinessCapabilities 多继承体系。
每个 detector 函数逻辑与原模块逐字一致，零偏差。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class _BusinessCapDetectorsMixin:
    """检测器 mixin：12 个业务能力 detector 方法。"""

    # -------------------------------------------------------------------
    # 12 个能力各自的 detector（规则实现，不调用 AI）
    # -------------------------------------------------------------------
    def _detect_login(self, elements: list[Any], goal_text: str) -> float:
        """登录：需要用户名/密码 输入框 + 登录按钮。"""
        try:
            goal = (goal_text or "").lower()
            if not any(
                k in goal for k in ["登录", "login", "登陆", "sign in", "signin"]
            ):
                return 0.0
            input_cnt = self._count_by_keyword(
                elements, ["用户名", "password", "密码", "账号", "email", "邮箱"]
            )
            btn_cnt = self._count_by_keyword(
                elements, ["登录", "login", "sign in", "submit"]
            )
            if input_cnt >= 2 and btn_cnt >= 1:
                return 0.9
            if input_cnt >= 1 and btn_cnt >= 1:
                return 0.65
            return 0.2
        except Exception as e:
            _log.warning("business_capabilities: _detect_login 异常 %s", e)
            return 0.0

    def _detect_search(self, elements: list[Any], goal_text: str) -> float:
        try:
            goal = (goal_text or "").lower()
            if not any(k in goal for k in ["搜索", "search", "筛选", "filter", "查询"]):
                return 0.0
            input_cnt = self._count_by_keyword(
                elements,
                ["搜索", "search", "筛选", "filter", "查询", "query", "keyword"],
            )
            btn_cnt = self._count_by_keyword(
                elements, ["搜索", "search", "筛选", "filter", "查询", "query"]
            )
            score = 0.0
            if input_cnt >= 1:
                score += 0.4
            if btn_cnt >= 1:
                score += 0.3
            if self._has_table(elements):
                score += 0.2
            return min(score + 0.1, 0.95)
        except Exception as e:
            _log.warning("business_capabilities: _detect_search 异常 %s", e)
            return 0.0

    def _detect_create(self, elements: list[Any], goal_text: str) -> float:
        try:
            goal = (goal_text or "").lower()
            if not any(
                k in goal
                for k in ["创建", "新增", "添加", "新建", "create", "add", "new"]
            ):
                return 0.0
            btn_cnt = self._count_by_keyword(
                elements, ["新增", "创建", "添加", "新建", "create", "add", "new", "+"]
            )
            if btn_cnt >= 1 and self._has_input(elements):
                return 0.85
            if btn_cnt >= 1:
                return 0.5
            return 0.15
        except Exception as e:
            _log.warning("business_capabilities: _detect_create 异常 %s", e)
            return 0.0

    def _detect_delete(self, elements: list[Any], goal_text: str) -> float:
        try:
            goal = (goal_text or "").lower()
            if not any(k in goal for k in ["删除", "delete", "remove", "移除", "del"]):
                return 0.0
            btn_cnt = self._count_by_keyword(
                elements, ["删除", "delete", "remove", "移除", "del"]
            )
            if btn_cnt >= 1 and self._has_table(elements):
                return 0.85
            if btn_cnt >= 1:
                return 0.55
            return 0.15
        except Exception as e:
            _log.warning("business_capabilities: _detect_delete 异常 %s", e)
            return 0.0

    def _detect_export(self, elements: list[Any], goal_text: str) -> float:
        try:
            goal = (goal_text or "").lower()
            if not any(k in goal for k in ["导出", "export", "下载", "download"]):
                return 0.0
            btn_cnt = self._count_by_keyword(
                elements, ["导出", "export", "下载", "download", "excel", "pdf", "csv"]
            )
            if btn_cnt >= 1:
                return 0.8
            return 0.2
        except Exception as e:
            _log.warning("business_capabilities: _detect_export 异常 %s", e)
            return 0.0

    def _detect_form_fill(self, elements: list[Any], goal_text: str) -> float:
        try:
            goal = (goal_text or "").lower()
            if not any(
                k in goal
                for k in ["填写", "填", "fill", "表单", "form", "录入", "输入"]
            ):
                return 0.0
            # 统计输入框数量
            input_cnt = sum(
                1
                for el in elements
                if self._element_tag(el) in ("input", "textarea", "select")
                or any(
                    k in self._element_text(el).lower()
                    for k in ["输入框", "input", "placeholder"]
                )
            )
            btn_cnt = self._count_by_keyword(
                elements, ["提交", "submit", "保存", "save", "确认", "confirm"]
            )
            if input_cnt >= 3 and btn_cnt >= 1:
                return 0.85
            if input_cnt >= 2:
                return 0.55
            return 0.2
        except Exception as e:
            _log.warning("business_capabilities: _detect_form_fill 异常 %s", e)
            return 0.0

    def _detect_data_query(self, elements: list[Any], goal_text: str) -> float:
        try:
            goal = (goal_text or "").lower()
            if not any(
                k in goal for k in ["查询", "query", "查找", "检索", "数据查询"]
            ):
                return 0.0
            if self._has_input(elements) and self._has_table(elements):
                return 0.8
            if self._has_table(elements):
                return 0.45
            return 0.2
        except Exception as e:
            _log.warning("business_capabilities: _detect_data_query 异常 %s", e)
            return 0.0

    def _detect_approval(self, elements: list[Any], goal_text: str) -> float:
        try:
            goal = (goal_text or "").lower()
            if not any(
                k in goal
                for k in ["审批", "审核", "approve", "同意", "驳回", "reject", "确认"]
            ):
                return 0.0
            btn_cnt = self._count_by_keyword(
                elements,
                ["同意", "approve", "驳回", "reject", "审批", "审核", "通过", "pass"],
            )
            if btn_cnt >= 1 and self._has_input(elements):
                return 0.85
            if btn_cnt >= 1:
                return 0.55
            return 0.2
        except Exception as e:
            _log.warning("business_capabilities: _detect_approval 异常 %s", e)
            return 0.0

    def _detect_batch(self, elements: list[Any], goal_text: str) -> float:
        try:
            goal = (goal_text or "").lower()
            if not any(k in goal for k in ["批量", "batch", "全选", "批量操作"]):
                return 0.0
            btn_cnt = self._count_by_keyword(
                elements, ["批量", "batch", "全选", "勾选", "checkbox"]
            )
            if btn_cnt >= 1 and self._has_table(elements):
                return 0.8
            if btn_cnt >= 1:
                return 0.45
            return 0.15
        except Exception as e:
            _log.warning("business_capabilities: _detect_batch 异常 %s", e)
            return 0.0

    def _detect_navigate(self, elements: list[Any], goal_text: str) -> float:
        try:
            goal = (goal_text or "").lower()
            if not any(
                k in goal
                for k in [
                    "导航",
                    "navigate",
                    "菜单",
                    "menu",
                    "进入",
                    "跳转",
                    "点击菜单",
                ]
            ):
                return 0.0
            link_cnt = self._count_by_keyword(
                elements,
                [
                    "导航",
                    "菜单",
                    "sidebar",
                    "menu",
                    "首页",
                    "管理",
                    "admin",
                    "dashboard",
                ],
            )
            if link_cnt >= 3:
                return 0.8
            if link_cnt >= 1:
                return 0.45
            return 0.2
        except Exception as e:
            _log.warning("business_capabilities: _detect_navigate 异常 %s", e)
            return 0.0

    def _detect_pagination(self, elements: list[Any], goal_text: str) -> float:
        try:
            goal = (goal_text or "").lower()
            if not any(
                k in goal
                for k in [
                    "分页",
                    "翻页",
                    "paging",
                    "pagination",
                    "下一页",
                    "上一页",
                    "page",
                ]
            ):
                return 0.0
            btn_cnt = self._count_by_keyword(
                elements,
                [
                    "下一页",
                    "上一页",
                    "next",
                    "prev",
                    "page",
                    "分页",
                    "翻页",
                    "10条",
                    "20条",
                    "50条",
                ],
            )
            if btn_cnt >= 2:
                return 0.85
            if btn_cnt >= 1:
                return 0.55
            return 0.15
        except Exception as e:
            _log.warning("business_capabilities: _detect_pagination 异常 %s", e)
            return 0.0

    def _detect_table_export(self, elements: list[Any], goal_text: str) -> float:
        try:
            goal = (goal_text or "").lower()
            if not any(
                k in goal
                for k in [
                    "表格导出",
                    "table export",
                    "导出表格",
                    "导出excel",
                    "导出pdf",
                    "导出csv",
                ]
            ):
                return 0.0
            if (
                self._has_table(elements)
                and self._count_by_keyword(
                    elements, ["excel", "pdf", "csv", "导出", "export", "下载"]
                )
                >= 1
            ):
                return 0.85
            return 0.25
        except Exception as e:
            _log.warning("business_capabilities: _detect_table_export 异常 %s", e)
            return 0.0
