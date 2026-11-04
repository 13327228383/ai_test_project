# -*- coding: utf-8 -*-
"""
测试用例存储模块常量 (v3.0)
"""

CASE_EXPORT_COLUMNS = [
    ("catalog_level", "目录层级", 10),
    ("catalog_name", "目录名称", 20),
    ("case_id", "用例ID", 14),
    ("title", "用例标题", 30),
    ("precondition", "预置条件", 40),
    ("test_steps", "测试步骤", 60),
    ("expected_result", "预期结果", 40),
    ("intent_summary", "用例摘要", 50),
    ("expected_outcome", "预期结果(旧)", 40),
    ("case_type", "用例类型", 12),
    ("risk_level", "风险等级", 10),
    ("auto_flag", "自动化标记", 12),
    ("threshold_flag", "门槛标记", 10),
    ("author", "用例建模作者", 12),
    ("created_date", "创建时间", 14),
    ("verification_status", "验证结果", 14),
    ("verification_time", "执行时间", 14),
    ("req_id", "需求编号", 14),
    ("checks", "检查点", 60),
    ("actions", "操作序列", 60),
]

# ★ 性能优化：DDL 只跑一次（首个连接触发），后续请求直接跳过
_case_tables_ensured = False
