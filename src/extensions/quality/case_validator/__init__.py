# -*- coding: utf-8 -*-
"""
Intent Validator - 测试用例校验器

按功能域拆分的子模块：
- constants: 校验常量与枚举
- result: 校验结果数据类
- prompts: 系统提示词模板注册
- utils: 通用工具函数
- rule_check: 快速规则校验（纯代码）
- ai_check: AI 深度校验（Flash 模型）
- cross_check: P0 防幻觉交叉校验（API/UI/枚举/追溯链路）
- feedback: 校验结果反馈格式化
"""

from .result import ValidationResult
from .rule_check import quick_rule_check
from .ai_check import ai_deep_check
from .cross_check import run_cross_checks
from .feedback import format_validation_feedback
from .main import validate_intents

__all__ = [
    "ValidationResult",
    "validate_intents",
    "format_validation_feedback",
    "quick_rule_check",
    "ai_deep_check",
    "run_cross_checks",
]
