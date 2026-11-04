# -*- coding: utf-8 -*-
"""
Intent Validator - 校验结果数据类
"""


class ValidationResult:
    def __init__(self):
        self.passed: bool = True
        self.total_cases: int = 0
        self.passed_intents: int = 0
        self.failed_intents: int = 0
        self.errors: list[dict] = []  # [{case_id, reason, severity}]
        self.warnings: list[dict] = []  # [{case_id, message}]
        self.summary: str = ""
        # ★ AI 健康度：交叉校验分类计数
        self.cross_check_counts: dict[str, int] = {
            "api": 0,
            "ui": 0,
            "enum": 0,
            "deep": 0,
            "rule": 0,
            "total_cases": 0,
        }

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "total": self.total_cases,
            "passed_count": self.passed_intents,
            "failed_count": self.failed_intents,
            "errors": self.errors,
            "warnings": self.warnings,
            "summary": self.summary,
            "cross_check_counts": self.cross_check_counts,
        }
