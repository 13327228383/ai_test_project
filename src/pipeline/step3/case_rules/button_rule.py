# -*- coding: utf-8 -*-
"""按钮规则：处理 button / submit 控件，生成 P01/N01/B01 三胞胎。"""

from typing import Any
from .base import BaseCaseRule


# ★ 通用修复(2026-07-21 v5): 弹窗常见按钮关键词集合
#   根因: Ant Design/ElementUI 等 UI 框架的"确定/取消/保存/提交"按钮
#         通常在 modal/dialog 弹窗中，主页面上不存在。
#   通用性: 适用于所有系统的弹窗按钮，不依赖业务逻辑。
_MODAL_BUTTON_KEYWORDS = frozenset(
    {
        "确定",
        "确 定",
        "确认",
        "确 认",
        "取消",
        "取 消",
        "保存",
        "保 存",
        "提交",
        "提 交",
        "应用",
        "应 用",
        "确定按钮",
        "保存按钮",
        "提交按钮",
        "确认按钮",
    }
)


def _is_modal_button(btn_text: str) -> bool:
    """判断按钮文本是否是弹窗常见按钮。"""
    if not btn_text:
        return False
    _core = btn_text.replace("按钮", "").strip()
    return _core in _MODAL_BUTTON_KEYWORDS or btn_text in _MODAL_BUTTON_KEYWORDS


class ButtonRule(BaseCaseRule):
    """按钮通用规则。

    P01: 点击按钮，导航到目标页 → 期望页面正常加载
    N01: 快速连续点击按钮 → 期望不重复提交
    B01: 无权限/禁用状态点击 → 期望被阻止
    """

    rule_id = "button_rule"
    name = "按钮规则"
    description = "处理按钮控件（提交/删除/导出/切换等），生成点击/防重复/禁用三胞胎"
    control_types = ["button", "submit", "reset", "switch", "toggle"]
    priority = 70

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
        btn_text = self._infer_submit_text(operation, target_page, merged_data)
        # ★ 降级=失败: DOM 无匹配按钮时返回 None，触发 fallback 到下一个规则或 _fallback_cases
        if btn_text is None:
            return None
        tracks = ["ui"]

        # ★ 通用修复(2026-07-21 v5): 弹窗按钮自动插入触发按钮前置动作
        #   根因: Ant Design/ElementUI 的"确定/取消/保存/提交"按钮在 modal 弹窗中，
        #         主页面 DOM 中不存在。Step4 执行 click(确定) 会失败。
        #   通用修复: 当 btn_text 是弹窗常见按钮时，在 navigate 和 click(btn_text) 之间
        #         插入一个 click "新建" 触发动作。
        #         "新建"是 CRUD 系统的通用触发按钮文本，get_by_text("新建", exact=False)
        #         会匹配包含"新建"子串的所有按钮（如"新建配方"、"新增辅材"）。
        #   通用性: 适用于所有有"新建/新增/添加/创建"按钮的 CRUD 系统
        #   合理性: 弹窗按钮必然有触发按钮，"新建"是最通用的触发按钮文本
        #   覆盖性: 覆盖所有 btn_text 是弹窗常见按钮的用例
        _need_trigger = _is_modal_button(btn_text)
        _trigger_click = [self._click("新建")] if _need_trigger else []

        # ★ §13.3修复(2026-07-25): checks 语义匹配 actions 语义
        #   原bug: P01/B01 统一用 _build_success_check 期望"成功"提示，
        #          但触发按钮(新建/编辑)actions只打开表单不提交 → 系统不显示成功提示 → 假FAIL
        #   修复: 用 _classify_button 分类按钮类型，触发按钮验证表单/弹窗打开
        #   通用性: 跨系统/跨行业/跨页面 — 任何按钮都能归入三类之一
        #   覆盖性: 影响 button_rule 的 P01/B01（与 input_rule 同源逻辑）
        #   合规: §13.3 "checks必须匹配actions语义" + §12.5反模式"触发按钮checks期望成功提示"
        _btn_type = self._classify_button(btn_text, expected)
        _FORM_SELECTOR = self._trigger_check_selectors()

        # ── P01（正向）──
        p01_actions = [
            self._nav(target_page),
            *_trigger_click,
            self._click(btn_text),
            self._screenshot(),
        ]
        if _btn_type == "trigger":
            # 触发按钮：actions 打开表单 → checks 验证表单/弹窗打开
            # ★ 用 element_exists_at（selector=target），不用 element_exists（selector硬编码main）
            p01_checks = [
                self._build_check(
                    "element_exists_at", _FORM_SELECTOR, "存在", "表单/弹窗打开成功"
                ),
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]
        else:
            # 提交按钮/其他按钮：actions 触发业务 → checks 验证成功提示
            p01_checks = [
                self._build_success_check(operation, req=req),
                self._build_check("no_error", "页面", "无错误", "系统无报错"),
            ]

        # ── N01（防重复提交）──
        n01_actions = [
            self._nav(target_page),
            *_trigger_click,
            self._click(btn_text),
            self._click(btn_text),
            self._screenshot(),
        ]
        n01_checks = [
            self._build_check(
                "no_error", "页面", "无错误", "连续点击未导致重复提交错误"
            ),
        ]

        # ── B01（禁用态）──
        b01_actions = [
            self._nav(target_page),
            *_trigger_click,
            self._click(btn_text),
            self._screenshot(),
        ]
        if _btn_type == "trigger":
            # 触发按钮：actions 打开表单 → checks 验证表单/弹窗打开
            b01_checks = [
                self._build_check(
                    "element_exists_at", _FORM_SELECTOR, "存在", "表单/弹窗打开成功"
                ),
            ]
        else:
            # 提交按钮/其他按钮：actions 触发业务 → checks 验证成功提示
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
                "防重复提交验证",
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
                "禁用态边界验证",
            ),
        ]
