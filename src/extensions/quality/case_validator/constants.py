# -*- coding: utf-8 -*-
"""
Intent Validator - 常量与配置
"""

# ── 快速规则校验（不调 AI，纯代码检查）──
VALID_TYPES = {
    "功能",
    "功能测试",
    "正向",
    "负向",
    "异常",
    "边界值",
    "UI",
    "安全",
    "易用性",
    "兼容性",
    "性能",
}
VALID_PRIORITIES = {"P0", "P1", "P2", "P3", "高", "中", "低"}
# v3.0 必填字段
REQUIRED_FIELDS = ["case_id", "type", "risk_level", "title"]
OPTIONAL_FIELDS = ["feature", "technique", "precondition"]

# v3.0 核心字段
INTENT_CORE_FIELDS = ["title", "type", "risk_level", "required_tracks"]

# 高风险关键词 → 必须有安全测试用例
SECURITY_KEYWORDS = [
    "输入",
    "提交",
    "上传",
    "导入",
    "搜索",
    "查询",
    "登录",
    "注册",
    "支付",
    "转账",
    "密码",
    "文件",
    "参数",
    "筛选",
    "编辑",
    "新增",
]
