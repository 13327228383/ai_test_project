# -*- coding: utf-8 -*-
from __future__ import annotations

"""
_registry.py — 能力注册表 + 管理 API
=====================================
作为 _BusinessCapRegistryMixin 提供给 BusinessCapabilities 多继承体系。
包含 CAPABILITY_REGISTRY 模板、__init__、list_capabilities、get_capability、register_capability。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class _BusinessCapRegistryMixin:
    """注册表 mixin：能力定义模板、初始化、管理 API。"""

    # -------------------------------------------------------------------
    # 能力注册表模板（detector 在 __init__ 中动态绑定）
    # -------------------------------------------------------------------
    CAPABILITY_REGISTRY: dict[str, dict[str, Any]] = {
        "login_with_password": {
            "capability_hint": "login_with_password",
            "display_name": "账号密码登录",
            "description": "定位用户名/密码输入框，填写后点击登录按钮并验证跳转。",
            "keywords": [
                "登录",
                "login",
                "登陆",
                "sign in",
                "signin",
                "用户名",
                "password",
            ],
            "min_elements": 3,
            "detector": None,  # 在 __init__ 中绑定到 self._detect_login
        },
        "search_or_filter": {
            "capability_hint": "search_or_filter",
            "display_name": "搜索/筛选",
            "description": "从搜索框或筛选区输入关键词并点击搜索/筛选按钮。",
            "keywords": ["搜索", "search", "筛选", "filter", "查询", "query", "查找"],
            "min_elements": 2,
            "detector": None,  # 在 __init__ 中绑定到 self._detect_search
        },
        "create_business_record": {
            "capability_hint": "create_business_record",
            "display_name": "创建业务记录",
            "description": "点击新增按钮，填写表单并提交，验证成功提示。",
            "keywords": ["创建", "新增", "添加", "新建", "create", "add", "new"],
            "min_elements": 3,
            "detector": None,  # 在 __init__ 中绑定到 self._detect_create
        },
        "delete_record": {
            "capability_hint": "delete_record",
            "display_name": "删除记录",
            "description": "定位目标行，点击删除按钮，确认弹窗并验证数据消失。",
            "keywords": ["删除", "delete", "remove", "移除", "del"],
            "min_elements": 2,
            "detector": None,  # 在 __init__ 中绑定到 self._detect_delete
        },
        "export_data": {
            "capability_hint": "export_data",
            "display_name": "导出数据",
            "description": "点击导出按钮，选择格式（若有），等待下载完成。",
            "keywords": ["导出", "export", "下载", "download", "excel", "pdf", "csv"],
            "min_elements": 1,
            "detector": None,  # 在 __init__ 中绑定到 self._detect_export
        },
        "form_fill_and_submit": {
            "capability_hint": "form_fill_and_submit",
            "display_name": "表单填写与提交",
            "description": "检测所有输入框，按 placeholder 填入测试数据后提交。",
            "keywords": [
                "填写",
                "fill",
                "表单",
                "form",
                "录入",
                "输入",
                "提交",
                "submit",
            ],
            "min_elements": 3,
            "detector": None,  # 在 __init__ 中绑定到 self._detect_form_fill
        },
        "data_query": {
            "capability_hint": "data_query",
            "display_name": "数据查询",
            "description": "填写查询条件，点击查询按钮，验证表格返回数据。",
            "keywords": ["查询", "query", "查找", "检索", "数据查询"],
            "min_elements": 3,
            "detector": None,  # 在 __init__ 中绑定到 self._detect_data_query
        },
        "approval_flow": {
            "capability_hint": "approval_flow",
            "display_name": "审批流操作",
            "description": "点击同意/驳回，填写审批意见并提交。",
            "keywords": ["审批", "审核", "approve", "同意", "驳回", "reject", "确认"],
            "min_elements": 2,
            "detector": None,  # 在 __init__ 中绑定到 self._detect_approval
        },
        "list_batch_action": {
            "capability_hint": "list_batch_action",
            "display_name": "列表批量操作",
            "description": "勾选多行，点击批量按钮并验证批量结果。",
            "keywords": ["批量", "batch", "全选", "批量操作", "checkbox"],
            "min_elements": 3,
            "detector": None,  # 在 __init__ 中绑定到 self._detect_batch
        },
        "navigate_menu": {
            "capability_hint": "navigate_menu",
            "display_name": "菜单导航",
            "description": "按层级点击侧边栏或顶部菜单项，进入目标页。",
            "keywords": ["导航", "navigate", "菜单", "menu", "sidebar", "跳转"],
            "min_elements": 3,
            "detector": None,  # 在 __init__ 中绑定到 self._detect_navigate
        },
        "pagination_control": {
            "capability_hint": "pagination_control",
            "display_name": "分页控制",
            "description": "点击下一页或调整每页条数，验证页面数据变化。",
            "keywords": [
                "分页",
                "翻页",
                "paging",
                "pagination",
                "下一页",
                "上一页",
                "page",
            ],
            "min_elements": 2,
            "detector": None,  # 在 __init__ 中绑定到 self._detect_pagination
        },
        "table_export": {
            "capability_hint": "table_export",
            "display_name": "表格导出",
            "description": "识别表格附近的 Excel / PDF / CSV 导出按钮，点击并验证下载。",
            "keywords": ["表格导出", "table export", "导出表格", "excel", "pdf", "csv"],
            "min_elements": 3,
            "detector": None,  # 在 __init__ 中绑定到 self._detect_table_export
        },
    }

    # -------------------------------------------------------------------
    # 初始化
    # -------------------------------------------------------------------
    def __init__(self):
        # 从模板深拷贝注册表
        self._registry: dict[str, dict[str, Any]] = {
            k: dict(v) for k, v in self.CAPABILITY_REGISTRY.items()
        }
        # 动态绑定 detector 方法引用
        self._registry["login_with_password"]["detector"] = self._detect_login
        self._registry["search_or_filter"]["detector"] = self._detect_search
        self._registry["create_business_record"]["detector"] = self._detect_create
        self._registry["delete_record"]["detector"] = self._detect_delete
        self._registry["export_data"]["detector"] = self._detect_export
        self._registry["form_fill_and_submit"]["detector"] = self._detect_form_fill
        self._registry["data_query"]["detector"] = self._detect_data_query
        self._registry["approval_flow"]["detector"] = self._detect_approval
        self._registry["list_batch_action"]["detector"] = self._detect_batch
        self._registry["navigate_menu"]["detector"] = self._detect_navigate
        self._registry["pagination_control"]["detector"] = self._detect_pagination
        self._registry["table_export"]["detector"] = self._detect_table_export
        # 最近一次判定结果
        self._last_result: tuple[str, float] | None = None
        _log.info(
            "BusinessCapabilities 已初始化，已注册 %d 个能力", len(self._registry)
        )

    # ------------------------------------------------------------------
    # 管理 API
    # ------------------------------------------------------------------
    def list_capabilities(self) -> list[str]:
        """返回所有已注册能力的 hint 列表。"""
        try:
            return sorted(self._registry.keys())
        except Exception as e:
            _log.warning("BusinessCapabilities.list_capabilities 异常 %s", e)
            return []

    def get_capability(self, hint: str) -> dict[str, Any] | None:
        """按 hint 读取能力定义（不含 detector callable）。"""
        try:
            raw = self._registry.get(hint)
            if not raw:
                return None
            # 浅拷贝一份，去掉 detector 避免调用方误判
            info = {k: v for k, v in raw.items() if k != "detector"}
            return info
        except Exception as e:
            _log.warning("BusinessCapabilities.get_capability 异常 %s", e)
            return None

    def register_capability(self, hint: str, def_dict: dict[str, Any]) -> bool:
        """动态注册新能力。def_dict 必须包含 capability_hint / keywords / min_elements / detector。"""
        try:
            if not hint or not def_dict:
                _log.warning("register_capability: 参数为空 hint=%s", hint)
                return False
            required = ("capability_hint", "keywords", "min_elements", "detector")
            for key in required:
                if key not in def_dict:
                    _log.warning("register_capability: 缺少必要字段 %s", key)
                    return False
            if not callable(def_dict["detector"]):
                _log.warning("register_capability: detector 必须可调用")
                return False
            self._registry[hint] = dict(def_dict)
            _log.info("动态注册业务能力: %s", hint)
            return True
        except Exception as e:
            _log.warning("register_capability 异常 %s", e)
            return False
