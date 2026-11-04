# -*- coding: utf-8 -*-
"""
Intent Validator - P0 防幻觉：UI 元素确定性校验 (v3.0)
========================================================
从 checks[{track=ui}].check 和 actions[{type=click/fill/select_option}].target_hint
提取 UI 元素名称，与 Figma 设计稿真实元素比对。

零 AI 调用 — 纯文本匹配 + MySQL 查询(Figma nav_map)。
"""

import json
import logging
import re

from ._data import load_cross_check_data

_log = logging.getLogger(__name__)

# 通用 UI 词汇白名单 — 这些词即使不在 Figma 中也是合理的
_GENERIC_UI_WORDS = {
    "按钮",
    "输入框",
    "下拉框",
    "菜单",
    "表格",
    "页面",
    "弹窗",
    "对话框",
    "搜索框",
    "文本框",
    "确认",
    "取消",
    "确定",
    "关闭",
    "保存",
    "提交",
    "登录",
    "登入",
    "注册",
    "退出",
    "返回",
    "下一步",
    "上一步",
    "新增",
    "编辑",
    "删除",
    "查看",
    "刷新",
    "导出",
    "导入",
    "上传",
    "下载",
    "筛选",
    "重置",
    "清空",
    "开始时间",
    "结束时间",
    "创建时间",
    "更新时间",
    "操作",
    "状态",
    "备注",
    "描述",
    "名称",
    "编号",
    "类型",
}


def cross_check_ui_elements(cases: list[dict]) -> list[dict]:
    """
    v3.0 确定性校验：从 checks[track=ui] 和 actions 中提取 UI 元素名称，
    与 Figma 设计稿 nav_map 中的真实页面元素比对。

    验证逻辑:
      1. checks[{track=ui}].check 中的文本元素 → 必须在 Figma nav_map 中存在
      2. actions[{type=click/fill/select_option/upload}].target_hint → 必须在 Figma 中存在

    返回: [{"case_id": "...", "reason": "...", "severity": "error|warning", "cross_check": "ui"}]
    """
    data = load_cross_check_data()
    figma_elements = data.get("figma_elements", set())

    if not figma_elements:
        return [
            {
                "case_id": "SYSTEM",
                "reason": "⚠️ Figma UI元素基准未加载，UI确定性校验已跳过。建议执行Step1加载Figma数据源。",
                "severity": "warning",
                "cross_check": "baseline",
            }
        ]

    errors = []

    for c in cases:
        if not isinstance(c, dict):
            continue
        cid = c.get("case_id", "?")

        # ── 1. 从 checks[track=ui] 提取 UI 元素 ──
        # ★ Fix20 (2026-07-07): 移除 checks 的 UI 元素 cross_check
        #   原bug: 用正则从 check 文本提取所有 2-10 字中文短语，
        #         检查它们是否在 Figma 设计稿中存在。
        #         但 check 文本是行为描述（如"检查辅材列表区域是否显示空状态提示"），
        #         提取出来的中文短语（"检查辅材列表区域是否"、"显示空状态提示"）
        #         不是 UI 元素名，是行为描述片段，自然在 Figma 中找不到。
        #         导致 92/92 AI 用例全部被 cross_check 阻断。
        #   修复: 只校验 actions[].target_hint（操作目标），不校验 checks 文本。
        #         actions 的 target_hint 是明确的 UI 元素引用，应该校验；
        #         checks 的文本是行为描述，不是 UI 元素引用，不应该校验。
        #   契约: §4.1 "防幻觉是唯一需要多步的地方" — 防幻觉应该针对 actions（操作目标），
        #         而不是 checks（行为描述）。
        checks = c.get("checks") or []
        if isinstance(checks, str):
            try:
                checks = json.loads(checks)
            except Exception:
                checks = []
        # ★ Fix20: checks 的 UI 元素校验已移除（行为描述不是 UI 元素引用）

        # ── 2. 从 actions 提取操作目标 ──
        actions = c.get("actions") or []
        if isinstance(actions, str):
            try:
                actions = json.loads(actions)
            except Exception:
                actions = []

        for ai, act in enumerate(actions if isinstance(actions, list) else []):
            if not isinstance(act, dict):
                continue
            act_type = str(act.get("type", "")).strip().lower()
            # navigate 由 _api.py 校验，这里只校验交互类 action
            if act_type in ("navigate", "verify", "wait", "scroll"):
                continue

            target = str(act.get("target_hint", "")).strip()
            if not target:
                errors.append(
                    {
                        "case_id": cid,
                        "reason": f"actions[{ai}].target 为空（type={act_type}），无法确定操作目标。",
                        "severity": "warning",
                        "cross_check": "ui",
                    }
                )
                continue

            # 如果 target 是通用词 → 跳过
            if target in _GENERIC_UI_WORDS:
                continue

            # 检查 target 是否在 Figma 元素中存在
            found = any(target in str(fe) or str(fe) in target for fe in figma_elements)
            if not found:
                suggestion = _find_closest_element(target, figma_elements)
                severity = (
                    "error"
                    if act_type in ("click", "fill", "select_option")
                    else "warning"
                )
                errors.append(
                    {
                        "case_id": cid,
                        "reason": (
                            f"actions[{ai}].target「{target}」未在Figma设计稿中找到。"
                            f"{'建议: ' + suggestion if suggestion else '请确认操作目标是否正确。'}"
                        ),
                        "severity": severity,
                        "cross_check": "ui",
                        "fix_hint": suggestion,
                    }
                )

    return errors


def _find_closest_element(target: str, candidates: set) -> str:
    """找最接近的 Figma 元素名。"""
    if not candidates or not target:
        return ""
    # 子串匹配
    for cand in sorted(candidates):
        cand_str = str(cand)
        if target in cand_str or cand_str in target:
            return cand_str
    # 字符相似度
    best = ""
    best_score = 0
    target_set = set(target)
    for cand in candidates:
        cand_str = str(cand)
        cand_set = set(cand_str)
        if not cand_set:
            continue
        score = len(target_set & cand_set) / len(target_set | cand_set)
        if score > best_score:
            best_score = score
            best = cand_str
    return best if best_score > 0.3 else ""
