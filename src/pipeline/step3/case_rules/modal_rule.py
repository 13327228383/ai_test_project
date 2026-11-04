# -*- coding: utf-8 -*-
"""模态框规则：处理 modal / dialog 控件，生成 P01/N01/B01 三胞胎。"""

from typing import Any
from .base import BaseCaseRule


class ModalRule(BaseCaseRule):
    """模态框/对话框通用规则。

    P01: 打开模态框，确认操作 → 期望成功关闭
    N01: 打开模态框，取消操作 → 期望关闭不执行
    B01: 快速连续打开关闭 → 期望不出现重复模态框
    """

    rule_id = "modal_rule"
    name = "模态框规则"
    description = "处理模态框/对话框控件，生成确认/取消/快速操作三胞胎"
    control_types = ["modal", "dialog", "popup", "confirm"]
    priority = 30

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
            self._click("操作按钮"),
            self._click("确认按钮"),
            self._screenshot(),
        ]
        p01_checks = [
            self._build_success_check(operation, req=req),
            self._build_check("no_error", "页面", "无错误", "系统无报错"),
        ]

        # ── N01（取消）──
        n01_actions = [
            self._nav(target_page),
            self._click("操作按钮"),
            self._click("取消按钮"),
            self._screenshot(),
        ]
        n01_checks = [
            self._build_check(
                "element_exists", "页面内容区域", "存在", "取消后回到原页面"
            ),
            self._build_check("no_error", "页面", "无错误", "系统无报错"),
        ]

        # ── B01（快速操作）──
        b01_actions = [
            self._nav(target_page),
            self._click("操作按钮"),
            self._click("取消按钮"),
            self._click("操作按钮"),
            self._click("确认按钮"),
            self._screenshot(),
        ]
        b01_checks = [
            self._build_success_check(operation, req=req),
            self._build_check("no_error", "页面", "无错误", "系统无报错"),
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
                "取消操作验证",
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
                "快速操作边界验证",
            ),
        ]
