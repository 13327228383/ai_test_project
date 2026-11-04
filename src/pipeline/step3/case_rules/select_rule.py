# -*- coding: utf-8 -*-
"""下拉选择规则：处理 select 控件，生成 P01/N01/B01 三胞胎。"""

from typing import Any
from .base import BaseCaseRule


class SelectRule(BaseCaseRule):
    """下拉选择通用规则。

    P01: 选择一个选项，提交 → 期望成功
    N01: 不选任何选项，提交 → 期望校验提示
    B01: 选择后切换选项，提交 → 期望更新成功
    """

    rule_id = "select_rule"
    name = "下拉框规则"
    description = "处理下拉选择框控件，生成选择/不选/切换三胞胎"
    control_types = ["select", "dropdown"]
    priority = 80

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
        submit_text = self._infer_submit_text(operation, target_page, merged_data)
        # ★ 降级=失败: DOM 无匹配按钮时返回 None，触发 fallback 到下一个规则或 _fallback_cases
        if submit_text is None:
            return None
        tracks = ["ui"]

        # ★ §13.3修复(2026-07-25): 用 _build_submit_checks 统一 checks 语义

        # ── P01（正向）──
        p01_actions = [
            self._nav(target_page),
            self._select("第一个选项"),
            self._click(submit_text),
            self._screenshot(),
        ]
        p01_checks = self._build_submit_checks(
            submit_text, operation, expected, req, check_type="success"
        )

        # ── N01（异常）──
        n01_actions = [
            self._nav(target_page),
            self._click(submit_text),
            self._screenshot(),
        ]
        n01_checks = self._build_submit_checks(
            submit_text, operation, expected, req, check_type="error_hint"
        )

        # ── B01（边界）──
        b01_actions = [
            self._nav(target_page),
            self._select("第一个选项"),
            self._select("第二个选项"),
            self._click(submit_text),
            self._screenshot(),
        ]
        b01_checks = self._build_submit_checks(
            submit_text, operation, expected, req, check_type="success"
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
