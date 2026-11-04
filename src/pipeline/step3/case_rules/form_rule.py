# -*- coding: utf-8 -*-
"""表单规则：处理多字段复合表单，生成 P01/N01/B01 三胞胎。"""

from typing import Any
from .base import BaseCaseRule


class FormRule(BaseCaseRule):
    """多字段表单通用规则。

    适用于包含多个输入字段的复合表单（新增/编辑页面）。
    P01: 填写所有必填字段，提交 → 期望成功
    N01: 只填部分字段，提交 → 期望校验提示
    B01: 填写超长字段，提交 → 期望截断或校验
    """

    rule_id = "form_rule"
    name = "表单规则"
    description = "处理多字段复合表单，生成全填/部分填/超长边界三胞胎"
    control_types = ["form", "multi_field", "composite"]
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
        submit_text = self._infer_submit_text(operation, target_page, merged_data)
        # ★ 降级=失败: DOM 无匹配按钮时返回 None，触发 fallback 到下一个规则或 _fallback_cases
        if submit_text is None:
            return None
        tracks = ["ui"]

        # ★ §13.3修复(2026-07-25): checks 语义匹配 actions 语义
        #   原bug: P01/N01/B01 统一期望"成功/校验提示"，但触发按钮 actions 只打开表单不提交 → 假FAIL
        #   修复: 用 _classify_button 分类，触发按钮验证表单/弹窗打开
        #   通用性: 跨系统/跨行业/跨页面 — 与 button_rule/input_rule 同源逻辑
        #   覆盖性: 影响 form_rule 的 P01/N01/B01
        #   合规: §13.3 "checks必须匹配actions语义" + §12.5反模式
        _btn_type = self._classify_button(submit_text, expected)
        _FORM_SELECTOR = self._trigger_check_selectors()

        # ★ §13.3修复(2026-07-25): 输入来源走 _infer_fill_value 统一管道，禁硬编码
        #   原bug: 硬编码 "测试数据"/"测试名称" 违反 §13.3 输入来源规则
        #   修复: 用 _infer_fill_target + _infer_fill_value 从 RAG/db 推断 target 和 value
        #   通用性: 跨系统/跨行业 — value_generator 内置密码/邮箱/手机/日期等语义
        #   覆盖性: 影响 form_rule 的 P01/N01/B01
        _fill_target = self._infer_fill_target(operation, target_page, merged_data)
        _fill_value = self._infer_fill_value(operation, merged_data)
        _name_target = "名称输入框"
        _name_value = self._infer_fill_value("名称", merged_data)

        # ── P01（正向）──
        p01_actions = [
            self._nav(target_page),
            self._fill(_fill_target, _fill_value),
            self._fill(_name_target, _name_value),
            self._click(submit_text),
            self._screenshot(),
        ]
        if _btn_type == "trigger":
            p01_checks = [
                self._build_check(
                    "element_exists_at", _FORM_SELECTOR, "存在", "表单/弹窗打开成功"
                ),
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]
        else:
            p01_checks = [
                self._build_success_check(operation, req=req),
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]

        # ── N01（部分填）──
        n01_actions = [
            self._nav(target_page),
            self._fill(_fill_target, _fill_value),
            self._click(submit_text),
            self._screenshot(),
        ]
        if _btn_type == "trigger":
            n01_checks = [
                self._build_check(
                    "element_exists_at", _FORM_SELECTOR, "存在", "表单/弹窗打开成功"
                ),
            ]
        else:
            n01_checks = [
                self._build_check(
                    "error_hint", "页面", "校验提示", "系统显示必填字段提示"
                ),
            ]

        # ── B01（超长）──
        # 值必须真正违反长度约束：此前调用未声明约束 → 走通用非法值得到 3 字符
        #   "!@#"，"超长校验提示"断言从未越界（边界用例空转）。
        try:
            from src.pipeline.step3.value_generator import (
                MAX_LENGTH_OVERFLOW,
                generate_invalid_value_from_type,
            )

            _long_text = generate_invalid_value_from_type(
                "string", "超长输入", constraint="max_length"
            )
        except Exception:
            _long_text = "A" * (MAX_LENGTH_OVERFLOW + 1)
        b01_actions = [
            self._nav(target_page),
            self._fill(_fill_target, _long_text),
            self._fill(_name_target, _long_text),
            self._click(submit_text),
            self._screenshot(),
        ]
        if _btn_type == "trigger":
            b01_checks = [
                self._build_check(
                    "element_exists_at", _FORM_SELECTOR, "存在", "表单/弹窗打开成功"
                ),
            ]
        else:
            b01_checks = [
                self._build_check(
                    "validation_hint", "页面", "校验提示", "系统显示超长校验提示"
                ),
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
                "必填字段缺失验证",
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
                "超长输入边界验证",
            ),
        ]
