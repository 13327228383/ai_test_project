# -*- coding: utf-8 -*-
"""日期规则：处理 date_picker / time_picker 控件，生成 P01/N01/B01 三胞胎。"""

import logging
from typing import Any
from .base import BaseCaseRule

_log = logging.getLogger(__name__)


class DateRule(BaseCaseRule):
    """日期/时间选择通用规则。

    P01: 选择合法日期，提交 → 期望成功
    N01: 输入非法日期格式，提交 → 期望错误提示
    B01: 选择边界日期（今天/极远未来），提交 → 期望校验
    """

    rule_id = "date_rule"
    name = "日期选择规则"
    description = "处理日期/时间选择器控件，生成合法/非法/边界三胞胎"
    control_types = ["date_picker", "date", "time_picker", "time", "datetime"]
    priority = 40

    def generate(
        self,
        req: dict[str, Any],
        target_page: str,
        control_type: str,
        merged_data: dict[str, Any],
        selected_ops: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rid = self._safe_rid(req)
        module = req.get("module", merged_data.get("module", "")) or ""
        operation = (req.get("operation") or "").lower()
        expected = req.get("expected_feedback") or req.get("expected") or ""
        desc = req.get("description", "") or operation
        fill_target = self._infer_fill_target(operation, target_page, merged_data)
        submit_text = self._infer_submit_text(operation, target_page, merged_data)
        # ★ 降级=失败: DOM 无匹配按钮时返回 None，触发 fallback 到下一个规则或 _fallback_cases
        if submit_text is None:
            return None
        tracks = ["ui"]

        # ── P01（正向）──
        # ★ §13.3 通用修复(2026-07-24 续): 走 value_generator 统一管道，禁硬编码日期
        try:
            from src.pipeline.step3.value_generator import generate_value_from_type

            _p01_date = generate_value_from_type("string", "日期")
        except Exception as _e:
            _log.warning(
                "[date_rule] value_generator 不可用，用兜底日期: %s", _e, exc_info=True
            )
            _p01_date = "2026-01-01"
        p01_actions = [
            self._nav(target_page),
            self._fill(fill_target, _p01_date),
            self._click(submit_text),
            self._screenshot(),
        ]
        p01_checks = self._build_submit_checks(
            submit_text, operation, expected, req, check_type="success"
        )

        # ── N01（异常）──
        # ★ §13.3 通用修复(2026-07-24 续): 走 value_generator 统一管道，禁硬编码非法日期
        try:
            from src.pipeline.step3.value_generator import (
                generate_invalid_value_from_type,
            )

            _n01_date = generate_invalid_value_from_type("string", "日期")
        except Exception as _e:
            _log.warning(
                "[date_rule] value_generator 不可用，用兜底非法日期: %s",
                _e,
                exc_info=True,
            )
            _n01_date = "invalid_date"
        n01_actions = [
            self._nav(target_page),
            self._fill(fill_target, _n01_date),
            self._click(submit_text),
            self._screenshot(),
        ]
        n01_checks = self._build_submit_checks(
            submit_text, operation, expected, req, check_type="error_hint"
        )

        # ── B01（边界）──
        b01_actions = [
            self._nav(target_page),
            self._fill(fill_target, ""),
            self._click(submit_text),
            self._screenshot(),
        ]
        b01_checks = self._build_submit_checks(
            submit_text, operation, expected, req, check_type="validation_hint"
        )

        return [
            self._build_one(
                f"TC-{rid}-P01",
                req,
                module,
                desc,
                operation,
                expected,
                "happy_path",
                "P1",
                "正向",
                "playwright",
                control_type,
                tracks,
                p01_actions,
                p01_checks,
                "正向流程验证",
            ),
            self._build_one(
                f"TC-{rid}-N01",
                req,
                module,
                desc,
                operation,
                expected,
                "error_path",
                "P1",
                "异常",
                "playwright",
                control_type,
                tracks,
                n01_actions,
                n01_checks,
                "异常输入校验",
            ),
            self._build_one(
                f"TC-{rid}-B01",
                req,
                module,
                desc,
                operation,
                expected,
                "boundary",
                "P2",
                "边界",
                "playwright",
                control_type,
                tracks,
                b01_actions,
                b01_checks,
                "边界值与异常验证",
            ),
        ]
