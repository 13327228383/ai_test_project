# -*- coding: utf-8 -*-
"""表格规则：处理 table 控件（CRUD 操作），生成 P01/N01/B01 三胞胎。"""

from typing import Any
from .base import BaseCaseRule


class TableRule(BaseCaseRule):
    """表格 CRUD 通用规则。

    P01: 执行标准操作（新增/编辑/删除），确认 → 期望成功
    N01: 操作不存在的行/空数据操作 → 期望提示
    B01: 批量操作/全选操作 → 期望正确
    """

    rule_id = "table_rule"
    name = "表格规则"
    description = "处理表格控件（增删改查），生成标准操作/空数据/批量操作三胞胎"
    control_types = ["table", "table_row", "crud", "list"]
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

        # 推断操作类型
        if any(k in operation for k in ["新增", "添加", "创建", "新建"]):
            act_btn = "新建按钮"
            act_check = "新建页面"
        elif any(k in operation for k in ["编辑", "修改", "更新"]):
            act_btn = "编辑按钮"
            act_check = "编辑页面"
        elif any(k in operation for k in ["删除", "移除"]):
            act_btn = "删除按钮"
            act_check = "确认删除对话框"
        else:
            act_btn = "操作按钮"
            act_check = "操作结果"

        # ── P01（正向）──
        p01_actions = [
            self._nav(target_page),
            self._click(act_btn),
            self._screenshot(),
        ]
        p01_checks = [
            self._build_success_check(operation, req=req),
            self._build_check("no_error", "页面", "无错误", "系统无报错"),
        ]

        # ── N01（空数据）──
        n01_actions = [
            self._nav(target_page),
            self._click("删除按钮"),
            self._screenshot(),
        ]
        n01_checks = [
            self._build_check("error_hint", "页面", "提示信息", "系统显示操作提示"),
        ]

        # ── B01（批量）──
        b01_actions = [
            self._nav(target_page),
            self._click("全选"),
            self._click("批量操作按钮"),
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
                "空数据操作验证",
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
                "批量操作边界验证",
            ),
        ]
