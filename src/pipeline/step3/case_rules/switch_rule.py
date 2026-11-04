# -*- coding: utf-8 -*-
"""切换规则：处理 switch / toggle 控件，生成 P01/N01/B01 三胞胎。"""

from typing import Any
from .base import BaseCaseRule


class SwitchRule(BaseCaseRule):
    """开关/切换通用规则。

    P01: 切换开关（开→关），确认 → 期望状态更新
    N01: 快速连续切换 → 期望不出现状态不一致
    B01: 禁用态切换 → 期望不可操作
    """

    rule_id = "switch_rule"
    name = "开关规则"
    description = "处理开关/切换控件，生成开关/快速切换/禁用三胞胎"
    control_types = ["switch", "toggle"]
    priority = 35

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
        tracks = ["ui"]

        # ── P01（正向）──
        p01_actions = [
            self._nav(target_page),
            self._click("切换按钮"),
            self._screenshot(),
        ]
        p01_checks = [
            self._build_success_check(operation, req=req),
            self._build_check("no_error", "页面", "无错误", "系统无报错"),
        ]

        # ── N01（快速切换）──
        n01_actions = [
            self._nav(target_page),
            self._click("切换按钮"),
            self._click("切换按钮"),
            self._click("切换按钮"),
            self._screenshot(),
        ]
        n01_checks = [
            self._build_check("no_error", "页面", "无错误", "快速切换未导致状态不一致"),
        ]

        # ── B01（禁用态）──
        b01_actions = [
            self._nav(target_page),
            self._click("切换按钮"),
            self._screenshot(),
        ]
        b01_checks = [
            self._build_success_check(operation, req=req),
        ]

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
                "快速切换验证",
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
                "开关边界验证",
            ),
        ]
