# -*- coding: utf-8 -*-
"""边界条件识别规则：校验阶段，识别需求中缺失的边界条件。"""

import re
from typing import Any
from .base import CheckRule


class InputFieldBoundaryRule(CheckRule):
    """输入字段边界识别规则。

    检查 operation 中是否包含输入字段相关词汇，如果包含但缺少长度/格式限制，
    则生成边界条件建议。
    """

    rule_id = "input_field_boundary"
    name = "输入字段边界识别"
    description = "检查输入字段是否缺少长度/格式限制等边界条件"
    priority = 70

    # 输入字段关键词
    _INPUT_FIELD_KEYWORDS = re.compile(
        r"输入|填写|录入|输入框|文本框|密码框|搜索框|用户名|密码|邮箱|手机号|身份证|银行卡",
        re.IGNORECASE,
    )

    # 边界条件关键词（已包含边界则不报警）
    _BOUNDARY_KEYWORDS = re.compile(
        r"长度|字符|格式|正则|最大|最小|不超过|限制|范围|位数|格式校验|非空|必填",
        re.IGNORECASE,
    )

    def apply(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        issues = []
        operation = req.get("operation", "")
        expected = req.get("expected_feedback", "")
        combined = f"{operation} {expected}"

        # 检查是否包含输入字段
        if not self._INPUT_FIELD_KEYWORDS.search(combined):
            return issues

        # 检查是否已包含边界条件
        if self._BOUNDARY_KEYWORDS.search(combined):
            return issues

        # 生成边界条件建议
        issues.append(
            {
                "type": "boundary_missing",
                "severity": "warning",
                "message": "输入字段缺少边界条件（长度/格式/范围）",
                "suggestion": "建议补充输入字段的长度限制、格式要求或取值范围",
            }
        )

        return issues


class NumericRangeBoundaryRule(CheckRule):
    """数值范围边界识别规则。

    检查 operation 中是否包含数值相关词汇，如果包含但缺少范围限制，
    则生成边界条件建议。
    """

    rule_id = "numeric_range_boundary"
    name = "数值范围边界识别"
    description = "检查数值字段是否缺少范围/单位等边界条件"
    priority = 65

    # 数值关键词
    _NUMERIC_KEYWORDS = re.compile(
        r"数量|金额|价格|总数|百分比|比例|折扣|库存|年龄|体重|身高|温度|"
        r"quantity|amount|price|total|percent|ratio|discount|stock|age",
        re.IGNORECASE,
    )

    # 范围关键词（已包含范围则不报警）
    _RANGE_KEYWORDS = re.compile(
        r"范围|最小|最大|区间|不低于|不超过|至少|至多|大于|小于|等于|单位|元|个|件|kg|℃",
        re.IGNORECASE,
    )

    def apply(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        issues = []
        operation = req.get("operation", "")
        expected = req.get("expected_feedback", "")
        combined = f"{operation} {expected}"

        # 检查是否包含数值字段
        if not self._NUMERIC_KEYWORDS.search(combined):
            return issues

        # 检查是否已包含范围条件
        if self._RANGE_KEYWORDS.search(combined):
            return issues

        # 生成边界条件建议
        issues.append(
            {
                "type": "boundary_missing",
                "severity": "warning",
                "message": "数值字段缺少范围/单位等边界条件",
                "suggestion": "建议补充数值的取值范围、单位或精度要求",
            }
        )

        return issues


class TimeConditionBoundaryRule(CheckRule):
    """时间条件边界识别规则。

    检查 operation 中是否包含时间相关词汇，如果包含但缺少时间范围，
    则生成边界条件建议。
    """

    rule_id = "time_condition_boundary"
    name = "时间条件边界识别"
    description = "检查时间条件是否缺少起止时间/有效期等边界条件"
    priority = 60

    # 时间关键词
    _TIME_KEYWORDS = re.compile(
        r"时间|日期|期限|有效期|截止|开始|结束|超时|倒计时|天数|月份|年份|"
        r"time|date|period|deadline|duration|timeout|day|month|year",
        re.IGNORECASE,
    )

    # 时间范围关键词（已包含时间范围则不报警）
    _TIME_RANGE_KEYWORDS = re.compile(
        r"起止|从.*到|至|之前|之后|以内|以外|天|小时|分钟|秒|月|年|具体|格式",
        re.IGNORECASE,
    )

    def apply(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        issues = []
        operation = req.get("operation", "")
        expected = req.get("expected_feedback", "")
        combined = f"{operation} {expected}"

        # 检查是否包含时间条件
        if not self._TIME_KEYWORDS.search(combined):
            return issues

        # 检查是否已包含时间范围
        if self._TIME_RANGE_KEYWORDS.search(combined):
            return issues

        # 生成边界条件建议
        issues.append(
            {
                "type": "boundary_missing",
                "severity": "warning",
                "message": "时间条件缺少起止时间/有效期等边界条件",
                "suggestion": "建议补充时间的起止范围、有效期或格式要求",
            }
        )

        return issues
