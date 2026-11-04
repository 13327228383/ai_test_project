# -*- coding: utf-8 -*-
"""
Intent Validator - P0 防幻觉：交叉校验汇总执行 (v3.0)
======================================================
v3.0 关键变化:
  - 所有子校验从 checks[] + actions[] 读数据（不再读废弃的 verification_plan）
  - 新增 _action 校验（action type 白名单 + target/value 合法性）
  - API/UI/DB/枚举 校验全部升级为确定性硬校验（零AI调用）
"""


from ..result import ValidationResult
from ._api import cross_check_apis
from ._ui import cross_check_ui_elements
from ._enum import cross_check_enums
from ._db_trace import cross_check_db_tables
from ._action import cross_check_actions


def run_cross_checks(cases: list[dict]) -> ValidationResult:
    """
    v3.0 P0 防幻觉：运行全部五个交叉校验并汇总结果。

    五个校验维度:
      1. API 确定性校验 — checks[track=api] + actions[type=navigate] ↔ YApi
      2. DB 确定性校验   — checks[track=db].check.sql 表名/列名 ↔ DB schema
      3. UI 确定性校验   — checks[track=ui] + actions[].target ↔ Figma nav_map
      4. 枚举确定性校验  — checks[].expect 值 ↔ DB enum_values
      5. Action 校验     — actions[].type 白名单 + target/value 完整性
    """
    result = ValidationResult()
    result.passed = True
    result.cross_check_counts["total_cases"] = len(cases)

    api_errors = cross_check_apis(cases)
    db_trace_errors = cross_check_db_tables(cases)
    ui_errors = cross_check_ui_elements(cases)
    enum_errors = cross_check_enums(cases)
    action_errors = cross_check_actions(cases)

    all_errors = []
    all_errors.extend(api_errors)
    all_errors.extend(db_trace_errors)
    all_errors.extend(ui_errors)
    all_errors.extend(enum_errors)
    all_errors.extend(action_errors)

    # ★ 记录分类计数
    result.cross_check_counts["api"] = len(api_errors)
    result.cross_check_counts["db_trace"] = len(db_trace_errors)
    result.cross_check_counts["ui"] = len(ui_errors)
    result.cross_check_counts["enum"] = len(enum_errors)
    result.cross_check_counts["action"] = len(action_errors)

    # ★ v3.0: 将 error 级问题提升为阻断（编造表名/API路径/action type → 阻断）
    critical_errors = [e for e in all_errors if e.get("severity") == "error"]
    warnings = [e for e in all_errors if e.get("severity") != "error"]

    if warnings:
        result.warnings.extend(warnings)

    if critical_errors:
        result.errors.extend(critical_errors)
        result.passed = False
        result.summary = (
            f"交叉校验发现 {len(critical_errors)} 个确定性错误(编造/不存在)"
            f"{' + ' + str(len(warnings)) + ' 个警告' if warnings else ''}"
        )
    elif warnings:
        result.summary = f"交叉校验发现 {len(warnings)} 个疑似问题"

    return result
