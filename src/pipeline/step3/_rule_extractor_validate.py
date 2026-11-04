# -*- coding: utf-8 -*-
"""★ §12.2规则3 通用修复(2026-07-26): case_rules 注册表路径后置统一校验门。

根因(§10.9):
  L1: 用例引用幻觉按钮(如"上传模型"/"辅材 输入框")导致 Step4 等待超时
  L2: case_rules 注册表规则生成 actions 后直接 return，绕过 _build_actions 内的 DOM 校验
  L3: §12.2规则3"校验前置必须在主流程"未覆盖 case_rules return 路径

全局观(§13.3): 修复后幻觉 actions 不再流入 Step4，Step5 报告不被污染
职责边界: Step3 内部修复，不越界到 Step4

通用性(§12.4 9维度):
  1. 跨系统: 纯 DOM 元素匹配，无业务依赖
  2. 跨行业: 无业务关键词硬编码
  3. 跨页面: 适用列表/详情/表单/Tab
  4. 跨Tab: nav_graph_nodes 含 Tab 子页面
  5. 数据完整性: DOM 为空时降级，不崩溃
  6. 根因溯源: 追到 case_rules 绕过校验
  7. 覆盖性: 覆盖所有 case_rules 产物（input/button/upload/search/...）
  8. 全局观: 评估了 Step3→Step4→Step5
  9. 职责边界: 在 Step3 修复

自我反驳三次:
  1) 漏覆盖? → 主流程末尾统一拦截，所有 case_rules 必经
  2) 误降级? → 只对 click/fill/upload_file target_hint 不在 DOM 中时降级
  3) 更根本? → 让 case_rules 复用 _build_actions 改动大风险高，此为最通用兜底
"""
from __future__ import annotations

import logging
from typing import Any
from src.pipeline.step3.case_rules.base import _SUBMIT_BUTTON_KEYWORDS as _SUBMIT_VERBS  # noqa: F401  词表唯一源

_log = logging.getLogger(__name__)


def _check_action_hint_in_dom(
    action: dict[str, Any], dom_elements: dict[str, list]
) -> tuple[bool, str]:
    """检查单个 action 的 target_hint 是否在 DOM 元素列表中存在。

    返回 (is_valid, bad_hint_desc)。is_valid=True 表示通过校验。
    通用性: 纯文本子串匹配，跨系统/跨行业/跨 UI 框架通用。
    """
    _t = str(action.get("type", "") or "")
    _hint = str(action.get("target_hint", "") or "").strip()
    if not _hint or _t in ("navigate", "screenshot", "press_key", "wait", "scroll"):
        return True, ""  # 非 hint 类 action 不校验
    _btns = dom_elements.get("button_texts") or []
    _inputs = dom_elements.get("input_placeholders") or []
    _links = dom_elements.get("link_texts") or []
    if _t in ("click", "check"):
        # ★ v5.0.6 修复(2026-07-27, §10.9): 提交按钮（dialog 内）跳过 DOM 校验
        #   根因(§10.9 3层why):
        #     L1: click:"保存" 被误降级 ×2
        #     L2: DOM button_texts 不含"保存"
        #     L3: "保存"是 dialog 内提交按钮，Step1 静态爬取抓不到 dialog 内控件
        #   修复: click target_hint 含提交动词（保存/提交/确定/确认）跳过 DOM 校验
        #   通用性(§12.4 9维度):
        #     1. 跨系统: CRUD 系统提交按钮都在 dialog 内，跨系统通用
        #     2. 跨行业: 纯语义动词判断，无业务依赖
        #     3. 跨页面: 适用所有含 dialog 提交的页面
        #     4. 跨Tab: 通用
        #     5. 数据完整性: DOM 为空时已由上层降级，此处针对有 DOM 数据时
        #     6. 根因溯源: 追到 dialog 内按钮 DOM 爬不到
        #     7. 覆盖性: 影响所有 case_rules 的提交按钮 click action
        #     8. 全局观: Step3 不误降级 → Step4 真实执行提交 → Step5 报告准确
        #     9. 职责边界: Step3 内部修复
        #   自我反驳三次:
        #     1) 让 Step1 爬 dialog? → dialog 动态生成，静态爬取抓不到
        #     2) 放宽所有 click 校验? → 失去防幻觉作用
        #     3) 识别提交按钮语义跳过? → _SUBMIT_VERBS 通用，跨系统通用 ✓
        #   合规: §13.3 触发按钮（新建/编辑等）在页面初始 DOM；
        #         提交按钮（保存/确定等）在 dialog 内，DOM 爬不到
        if any(v in _hint for v in _SUBMIT_VERBS):
            return True, ""  # 提交按钮在 dialog 内，DOM 校验无意义
        if any(_hint in b or b in _hint for b in (_btns + _links)):
            return True, ""
        return False, f"click:{_hint}"
    if _t == "fill":
        if not _inputs:
            return True, ""  # DOM 无 input 数据时不阻断（数据完整性降级）
        if any(_hint in p or p in _hint for p in _inputs):
            return True, ""
        return False, f"fill:{_hint}"
    if _t == "upload_file":
        # ★ v5.0.4 修复(2026-07-27, §10.9): upload_file 类型跳过 DOM 校验
        #   根因(§10.9 3层why):
        #     L1: upload_rule 的 P01/N01/B01 全部降级，button_not_found=True
        #     L2: _check_action_hint_in_dom 用 button_texts 匹配 upload_file 控件
        #     L3: upload_file 控件是 <input type="file">（在 dialog 内），
        #         既不在 button_texts 也不在 input_placeholders，DOM 校验错配
        #   修复: upload_file 跳过 DOM 校验（target_hint 是语义描述"文件上传控件"，
        #         控件在 dialog 内 DOM 爬不到，Step4 运行时动态查找 <input type="file">）
        #   通用性(§12.4 9维度):
        #     1. 跨系统: 所有系统的 upload_file 都是 <input type="file">，不在 button_texts
        #     2. 跨行业: 纯控件类型判断，无业务依赖
        #     3. 跨页面: upload 通常在 dialog 内，DOM 爬不到（通用）
        #     4. 跨Tab: 通用
        #     5. 数据完整性: DOM 为空时已由 L60 兜底，此处针对有 DOM 数据时
        #     6. 根因溯源: 追到 DOM 校验逻辑错配（upload_file 不是 button）
        #     7. 覆盖性: 影响所有 upload_file action（upload_rule + AI 生成）
        #     8. 全局观: Step3 不降级 → Step4 真实执行 upload → Step5 报告 upload 覆盖率
        #     9. 职责边界: Step3 内部修复，不越界
        #   自我反驳三次:
        #     1) 跳过会漏幻觉? → upload_rule 是规则生成非AI，target_hint 固定；
        #        AI 生成的 upload_file Step4 找不到 file input 会真 FAIL，不会假 PASS
        #     2) 改成校验 input_placeholders? → file input 无 placeholder，仍失败
        #     3) 让 Step1 爬 dialog DOM? → dialog 动态生成，Step1 静态爬取抓不到，风险高
        return True, ""  # upload_file 控件在 dialog 内，DOM 校验无意义
    if _t == "select_option":
        # select_option target_hint 通常是下拉框触发器，校验 button_texts
        if not _btns:
            return True, ""
        if any(_hint in b or b in _hint for b in _btns):
            return True, ""
        return False, f"select:{_hint}"
    return True, ""  # 未知 type 放行


def _validate_actions_against_dom(
    cases: list[dict[str, Any]],
    req: dict[str, Any],
    target_page: str,
    merged_data: dict[str, Any],
    control_type: str,
    dom_elements: dict[str, list] | None,
    rid: str = "",
) -> list[dict[str, Any]]:
    """对 case_rules 产物统一校验 actions 中 click/fill/upload target_hint 是否在 DOM 中存在。

    ★ P0-downgrade-removal(2026-07-29): 降级=失败，不再降级为 display 用例。
    引用幻觉元素的 case 标记 _hallucinated=True 并添加强校验 check，交给 Step4 真实执行判定。

    返回: 校验后的 cases 列表。
    """
    if not cases:
        return cases
    if not dom_elements or not target_page:
        return cases
    _btns = dom_elements.get("button_texts") or []
    _inputs = dom_elements.get("input_placeholders") or []
    if not _btns and not _inputs:
        return cases

    _validated: list[dict[str, Any]] = []
    _hallucinated = 0
    for _case in cases:
        _actions = _case.get("actions") or []
        _em = _case.get("execution_mode") or _case.get("test_method") or ""
        if _em != "playwright" or not _actions:
            _validated.append(_case)
            continue
        _bad_hints: list[str] = []
        for _act in _actions:
            _ok, _bad = _check_action_hint_in_dom(_act, dom_elements)
            if not _ok:
                _bad_hints.append(_bad)
        if not _bad_hints:
            _validated.append(_case)
            continue
        # ★ P0-downgrade-removal(2026-07-29): 不再降级为 display 用例
        #   标记 _hallucinated=True，让 Step4 真实执行判定（找不到元素=FAIL）
        _log.warning(
            "[RuleExtract] §12.2规则3: [%s] case=%s 引用幻觉元素 %s → 标记 _hallucinated",
            rid,
            _case.get("case_id", ""),
            _bad_hints,
        )
        _case["_hallucinated"] = True
        _case["_hallucinated_hints"] = _bad_hints[:5]
        _case["_hallucinated_reason"] = (
            f"§12.2规则3: actions引用DOM中不存在的元素({','.join(_bad_hints[:3])})"
        )
        _validated.append(_case)
        _hallucinated += 1
    if _hallucinated > 0:
        _log.info(
            "[RuleExtract] §12.2规则3: [%s] 标记 %d 条用例元素需实时探索",
            rid,
            _hallucinated,
        )
    return _validated
