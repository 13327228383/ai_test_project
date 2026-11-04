# -*- coding: utf-8 -*-
"""引擎自身逻辑表名唯一事实源 —— 清理/权限校验共用，勿在下游重抄。"""

KNOWN_LOGICAL_TABLES = (
    "coverage_results",
    "executions",
    "generated_reports",
    "requirements",
    "self_heal_patches",
    "test_cases",
)
