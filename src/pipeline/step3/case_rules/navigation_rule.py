# -*- coding: utf-8 -*-
"""导航规则：处理 navigation 控件，生成 P01/N01/B01 三胞胎。"""

from typing import Any
from .base import BaseCaseRule


class NavigationRule(BaseCaseRule):
    """页面导航通用规则。

    P01: 导航到目标页面 → 期望页面正常加载
    N01: 导航到不存在的页面 → 期望 404 或错误提示
    B01: 快速切换页面 → 期望不出现加载冲突
    """

    rule_id = "navigation_rule"
    name = "导航规则"
    description = "处理页面导航控件，生成正常导航/404/快速切换三胞胎"
    control_types = ["navigation", "nav", "tab", "menu", "breadcrumb"]
    priority = 25

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
            self._screenshot(),
        ]
        p01_checks = [
            self._build_check("element_exists", "页面内容区域", "存在", "页面正常加载"),
            self._build_check("no_error", "页面", "无错误", "系统无报错"),
        ]

        # ── N01（404）──
        # ★ v3.0 (2026-07-10): 用通用测试路径替代硬编码（原 /nonexistent-page-404）
        n01_actions = [
            self._nav("/__test_nonexistent_page__"),
            self._screenshot(),
        ]
        n01_checks = [
            self._build_check(
                "element_exists", "错误页面或提示", "存在", "系统显示404或错误提示"
            ),
        ]

        # ── B01（快速切换）──
        b01_actions = [
            self._nav(target_page),
            self._nav(target_page),
            self._screenshot(),
        ]
        b01_checks = [
            self._build_check(
                "element_exists", "页面内容区域", "存在", "快速切换后页面正常"
            ),
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
                "404页面验证",
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
                "快速切换边界验证",
            ),
        ]
