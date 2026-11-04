# -*- coding: utf-8 -*-
"""
表模式推断配置 (TablePatternsConfig)
=====================================
★ 将硬编码的表模式推断改为配置驱动，支持不同项目。

使用方式：
    from src.extensions.db.table_patterns_config import TABLE_PATTERNS_CONFIG

    # 获取当前项目的配置
    patterns = TABLE_PATTERNS_CONFIG.get("default", {})

配置结构：
    {
        "project_name": {
            "table_mappings": {
                "logical_name": ["TABLE_NAME_1", "TABLE_NAME_2"],
                ...
            },
            "foreign_key_hints": {
                "COLUMN_NAME": "TARGET_TABLE",
                ...
            },
            "code_column_patterns": ["*_CODE", "CODE"],
            "name_column_patterns": ["*_NAME", "NAME"],
        }
    }
"""

from typing import Any

# ══════════════════════════════════════════════════════════
# 默认配置（通用项目）
# ══════════════════════════════════════════════════════════
DEFAULT_CONFIG = {
    "table_mappings": {
        # 用户/权限模块
        "sys_user": ["SYS_USER", "USER", "ACCOUNT", "T_USER", "U_USER"],
        "sys_role": ["SYS_ROLE", "ROLE", "T_ROLE"],
        "sys_menu": ["SYS_MENU", "MENU", "NAVIGATION", "T_MENU"],
        "sys_dept": ["SYS_DEPT", "DEPARTMENT", "DEPT", "ORG", "T_DEPT"],
        # 业务主数据
        "product": [
            "PRODUCT",
            "PRODUCT_INFO",
            "GOODS",
            "ITEM",
            "SKU",
            "SPU",
            "T_PRODUCT",
        ],
        "brand": ["BRAND", "BRAND_INFO", "T_BRAND"],
        "category": ["CATEGORY", "CATEGORY_INFO", "CLASS", "TYPE", "T_CATEGORY"],
        "supplier": ["SUPPLIER", "SUPPLIER_INFO", "VENDOR", "T_SUPPLIER"],
        "customer": ["CUSTOMER", "CUSTOMER_INFO", "CLIENT", "T_CUSTOMER"],
        # 订单/交易
        "order": ["ORDER", "ORDER_INFO", "T_ORDER", "ORDERS"],
        "order_item": ["ORDER_ITEM", "ORDER_DETAIL", "ORDER_LINE", "T_ORDER_ITEM"],
        "payment": ["PAYMENT", "PAY", "T_PAYMENT"],
        "invoice": ["INVOICE", "BILL", "T_INVOICE"],
        # 库存/仓储
        "inventory": ["INVENTORY", "STOCK", "WAREHOUSE", "T_INVENTORY"],
        "warehouse": ["WAREHOUSE", "STORE", "DEPOT", "T_WAREHOUSE"],
        # 任务/流程
        "task": ["TASK", "JOB", "WORK_ITEM", "T_TASK"],
        "workflow": ["WORKFLOW", "PROCESS", "FLOW", "T_WORKFLOW"],
        # 字典/配置
        "dict": ["DICT", "DICTIONARY", "CONFIG", "SETTING", "T_DICT"],
        "param": ["PARAM", "PARAMETER", "CONFIG_PARAM", "T_PARAM"],
        # 日志/审计
        "log": ["LOG", "SYS_LOG", "AUDIT_LOG", "OPERATION_LOG", "T_LOG"],
        "audit": ["AUDIT", "AUDIT_TRAIL", "T_AUDIT"],
        # 文件/附件
        "file": ["FILE", "ATTACHMENT", "DOCUMENT", "T_FILE"],
        # 消息/通知
        "message": ["MESSAGE", "MSG", "NOTIFICATION", "NOTICE", "T_MESSAGE"],
        # 通用扩展
        "ext": ["EXT", "EXTENSION", "EXTRA", "T_EXT"],
    },
    "foreign_key_hints": {
        "USER_ID": "SYS_USER",
        "ROLE_ID": "SYS_ROLE",
        "DEPT_ID": "SYS_DEPT",
        "MENU_ID": "SYS_MENU",
        "PARENT_ID": None,  # 自引用，动态推断
        "CREATE_BY": "SYS_USER",
        "UPDATE_BY": "SYS_USER",
        "OWNER_ID": "SYS_USER",
        "PRODUCT_ID": "PRODUCT",
        "BRAND_ID": "BRAND",
        "CATEGORY_ID": "CATEGORY",
        "SUPPLIER_ID": "SUPPLIER",
        "CUSTOMER_ID": "CUSTOMER",
        "ORDER_ID": "ORDER",
        "TASK_ID": "TASK",
        "FILE_ID": "FILE",
    },
    "code_column_patterns": ["*_CODE", "CODE", "NO", "*_NO", "NUM", "*_NUM"],
    "name_column_patterns": ["*_NAME", "NAME", "TITLE", "LABEL", "*_TITLE"],
    "id_column_patterns": ["ID", "*_ID", "CODE", "NO", "*_NO"],
}

# ══════════════════════════════════════════════════════════
# 某业务系统行业专用配置（向后兼容）
# ══════════════════════════════════════════════════════════
TOBACCO_CONFIG = {
    "table_mappings": {
        "brand_info": ["BRAND_INFO", "PRODUCT_INFO", "GOODS_INFO", "BRAND"],
        "tobacco_info": ["TOBACCO_INFO", "LEAF_INFO", "MATERIAL_INFO", "TOBACCO"],
        "formulation_info": [
            "FORMULATION_BASIC_INFO",
            "RECIPE_INFO",
            "FORMULA_INFO",
            "FORMULATION",
        ],
        "product_task": ["PRODUCT_TASK", "BRAND_TASK"],
        "tobacco_task": ["TOBACCO_TASK", "LEAF_TASK"],
        "sys_user": ["SYS_USER", "USER", "ACCOUNT"],
        "origin": ["TOBACCO_ORIGIN", "ORIGIN"],
        "grade": ["TOBACCO_GRADE", "GRADE"],
        "year": ["TOBACCO_YEAR", "YEAR"],
    },
    "foreign_key_hints": {
        "ROLE_ID": "SYS_ROLE",
        "FILE_ID": "FILE",
        "PARENT_ID": None,
        "MENUS_ID": "SYS_MENU",
        "BRAND_ID": "BRAND_INFO",
        "TOBACCO_ID": "TOBACCO_INFO",
        "FORMULATION_ID": "FORMULATION_INFO",
    },
    "code_column_patterns": ["*_CODE", "CODE"],
    "name_column_patterns": ["*_NAME", "NAME"],
    "id_column_patterns": ["ID", "*_ID"],
}

# ══════════════════════════════════════════════════════════
# 配置注册表
# ══════════════════════════════════════════════════════════
TABLE_PATTERNS_CONFIG: dict[str, dict[str, Any]] = {
    "default": DEFAULT_CONFIG,
    "tobacco": TOBACCO_CONFIG,
}


def get_table_patterns(project_type: str = None) -> dict[str, Any]:
    """
    获取指定项目类型的表模式配置。

    Args:
        project_type: 项目类型标识。如果为None，尝试从config读取。

    Returns:
        表模式配置字典
    """
    if project_type is None:
        try:
            from src.core.config_loader import config

            project_type = getattr(config, "project_type", "default")
        except Exception:
            project_type = "default"

    return TABLE_PATTERNS_CONFIG.get(project_type, DEFAULT_CONFIG)
