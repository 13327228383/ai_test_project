# -*- coding: utf-8 -*-
"""
Intent Validator - 校验结果反馈格式化
"""

from .result import ValidationResult


def format_validation_feedback(result: ValidationResult) -> str:
    """将校验结果格式化为可注入重生成 prompt 的反馈文本。"""
    if result.passed and not result.warnings:
        return ""

    lines = ["## ⚠️ 上一轮测试用例校验反馈（请针对以下问题修正后重新生成）"]
    if result.errors:
        lines.append("\n### 必须修复的错误:")
        for e in result.errors:
            lines.append(f"- [{e.get('case_id','?')}] {e.get('reason','')}")

    # 分离交叉校验警告和普通警告
    cross_warnings = [w for w in result.warnings if w.get("cross_check")]
    normal_warnings = [w for w in result.warnings if not w.get("cross_check")]

    if normal_warnings:
        lines.append("\n### 建议优化的警告:")
        for w in normal_warnings:
            lines.append(f"- [{w.get('case_id','?')}] {w.get('message','')}")

    if cross_warnings:
        lines.append("\n### ⚡ 数据源交叉校验（API/UI/枚举疑似幻觉，请核实）:")
        for w in cross_warnings:
            lines.append(
                f"- [{w.get('case_id','?')}] [{w.get('cross_check','?').upper()}] {w.get('reason','')}"
            )
    return "\n".join(lines)
