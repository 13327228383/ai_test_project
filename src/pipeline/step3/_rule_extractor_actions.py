# -*- coding: utf-8 -*-
"""_rule_extractor_actions — 从 _rule_extractor.py 拆分（D.1g 架构债清理 2026-07-25，§4.3 单文件≤800行）。

职责：actions 相关函数。
逻辑不变，仅结构重构。re-export 保持调用兼容。
"""
from __future__ import (
    annotations,
)  # ★ 修复(2026-07-25): Python 3.9 兼容（PROJECT_MEMORY.md 硬约束）
import json
import logging
import re
from collections import Counter
from typing import Any
from src.pipeline.step3.case_rules.button_resolver import _SUBMIT_VERBS as _SUBMIT_VERBS  # noqa: F401  词表唯一源
from src.pipeline.step3.case_rules.button_resolver import _TRIGGER_VERBS as _TRIGGER_VERBS  # noqa: F401  词表唯一源

_log = logging.getLogger(__name__)

from ._rule_extractor_buttons import (  # noqa: F401
    _extract_operation_objects,
    _operation_object_matches_page,
    _match_button_by_keywords,
    _match_input_by_field_name,
    # ★ 2026-09-27: 变更型操作「触发→录入→提交」状态机的唯一实现（SSOT）。
    build_interaction_actions,
    infer_confirm_hint,
    classify_operation_category,
    mutation_flow_kind,
)

from ._rule_extractor_dom import (  # noqa: F401
    _extract_searchable_data_from_dom,
    _load_dom_elements_for_page,
    _load_nav_flat_list,
    _find_page_for_button,
)

from ._rule_extractor_nav import (  # noqa: F401
    _find_nav_path_for_req,
    _find_sidebar_for_target,
    _verify_sidebar_in_dom,
    _find_page_for_navigation,
    _score_nav_match,
    _validate_nav_hint_against_navmap,
    _build_nav_intent_hint,
)

from ._rule_extractor_utils import (  # noqa: F401
    _load_figma_confirm_nodes,
    _build_figma_ui_checks,
    _build_one_case,
    _extract_page_paths_from_rag,
    _match_page_for_requirement,
)


def _build_actions(
    req: dict,
    target_page: str,
    control_type: str,
    rag_data: dict,
    selected_ops: list[dict],
    error_case: bool = False,
    boundary_case: bool = False,
) -> list[dict]:
    """构建 actions 列表（模拟人类测试步骤）。

    ★ Fix47 (2026-07-09): 从 DOM 快照中提取实际按钮文本/输入框占位符作为 target_hint，
    而非泛化描述（如"查询按钮"→"批量打标"）。通用性：用关键词匹配 DOM 元素，不硬编码。
    ★ 治本修复 (2026-07-17): 当 target_page 为 "/" 时，三步策略：
      1. 从 page_dom_snapshots 搜索匹配页面路径 → 直接导航
      2. 从 nav_map 查找菜单导航路径 → 逐级点击菜单
      3. 以上都失败 → 兜底 navigate("/")
      根因：nav_map 的 URL 为空（SPA 应用），导致 _match_page_path 返回 "/"，
      测试用例导航到仪表盘而非正确页面，按钮匹配失败。
    """
    operation = str(req.get("operation", "") or "").strip()
    actions = []

    # ★ 治本修复 (2026-07-17): 三步页面匹配策略
    _nav_url = target_page  # 实际导航 URL
    _nav_menu_clicks = []  # 菜单导航点击序列（当无直接 URL 时使用）
    _sidebar_click_text = ""  # ★ 通用修复(2026-07-20 v2): 侧边栏菜单文本（URL不变时用）

    # ★ 通用修复(2026-07-20 v4): 策略3优先 - 优先使用 v4 前置匹配结果，避免重复查询
    #   ★ v4 修复根因: v3 在 _build_actions 内调用 _find_sidebar_for_target，但降级检查在
    #     _build_actions 之前，降级时 _build_actions 不执行，v3 fix 失效。
    #   v4 修复: extract_cases_by_rules 主流程已前置调用 _find_sidebar_for_target，
    #     结果存入 req["_v4_sidebar_click_text"] 和 req["_v4_real_url"]。
    #     _build_actions 直接读取，避免重复查询。
    #   通用性: 适用于所有"侧边栏菜单点击后URL不变"的SPA系统。
    #   合理性: 追根溯源 - v3 时机错误是根因，v4 前置 + 复用结果是治本修复。
    #   覆盖性: 兼容 v4 前置匹配成功、v4 未匹配但 v3 兜底、策略1/2 三种场景。
    _v4_sidebar = req.get("_v4_sidebar_click_text", "") or ""
    _v4_real_url = req.get("_v4_real_url", "") or ""
    if _v4_sidebar and _v4_real_url:
        # v4 前置匹配已成功，直接复用
        _sidebar_click_text = _v4_sidebar
        _nav_url = _v4_real_url
        target_page = _v4_real_url
        _log.info(
            "[RuleExtract] 治本修复(v4复用): req='%s' → real_url='%s' + sidebar='%s'",
            operation[:30],
            _v4_real_url[:60],
            _v4_sidebar,
        )
    else:
        # v4 未匹配，尝试 v3 兜底（_build_actions 被直接调用时，如 _fallback_cases）
        _sb_match = _find_sidebar_for_target(target_page, req)
        if _sb_match:
            _real_url, _sidebar_click_text = _sb_match
            _nav_url = _real_url
            target_page = _real_url
            _log.info(
                "[RuleExtract] 治本修复(v3兜底): req='%s' → real_url='%s' + sidebar='%s'",
                operation[:30],
                _real_url[:60],
                _sidebar_click_text,
            )
        else:
            # 策略1/2 仅在策略3未命中时执行
            if not target_page or target_page == "/" or target_page == "/login":
                # 策略1: 从 page_dom_snapshots 搜索匹配的页面路径
                _best_page = _find_page_for_navigation(req)
                if _best_page:
                    _nav_url = _best_page
                    _log.info(
                        "[RuleExtract] 治本修复(策略1): target_page='%s' → DOM页面 '%s'",
                        target_page,
                        _best_page,
                    )
                    target_page = _best_page
                else:
                    # 策略2: 从 nav_map 查找菜单导航路径
                    _nav_path = _find_nav_path_for_req(req)
                    if _nav_path:
                        _nav_menu_clicks = [
                            {"type": "click", "target_hint": menu_name}
                            for menu_name in _nav_path
                        ]
                        _log.info(
                            "[RuleExtract] 治本修复(策略2): target_page='%s' → 菜单路径 %s",
                            target_page,
                            _nav_path,
                        )

    # ★ Fix47: 加载目标页面的 DOM 元素（按钮文本、输入框占位符）
    dom_elements = _load_dom_elements_for_page(target_page)

    # Step 1: navigate（通用）
    # ★ P0-fix(2026-07-25): 删除 ADR-017 intent: 语义前缀，符合 §12.2规则1 + §12.1
    #   原bug(ADR-017): navigate target_hint 用 "intent:xxx" 语义前缀，让 Step4 实时菜单导航
    #   根因(§10.9 3层why):
    #     L1: 为什么用 intent:? → ADR-017 决策让 Step4 实时菜单导航
    #     L2: 为什么让 Step4 导航? → 预爬取架构（Path A）nav_map 不完整，URL 缺失
    #     L3: 为什么 nav_map 不完整? → Step1 爬取缺陷（菜单未展开/Tab未切换）
    #   但行 65-102 已通过 v4前置/v3兜底/策略1DOM/策略2nav_map 四重匹配确定 _nav_url
    #   全局观(§10.8): intent: 前缀让 Step4 重新做菜单导航决策 → 违反 §12.1"Step4只执行不决策"
    #     → 影响整条流水线：Step4 超时/菜单搜索耗时/决策错位
    #   职责边界(§13.3): Step3 职责是生成可执行用例，navigate 必须用 nav_map 真实值
    #   修复: navigate target_hint 用 _nav_url(URL直接导航) 优先 + target_page(页面名) 次之 + "/" 兜底
    #   通用性(§12.4):
    #     - 跨系统: React/Vue/Angular/原生HTML — 所有系统都有 nav_map，匹配失败降级首页
    #     - 跨行业: 无业务硬编码
    #     - 跨页面: 列表/详情/表单/Tab页 — _nav_url 已含 Tab 参数
    #     - 数据完整性: _nav_url 为空时降级到 target_page 或 "/"，不崩溃
    #   覆盖性: 覆盖 _build_actions 主流程（与 _fallback_cases.py 同源逻辑）
    #   合规: §12.2规则1"navigate target_hint 必须在 system_nav_maps 中存在" + §12.1"Step4只执行不决策"
    _nav_hint = _nav_url or target_page or "/"
    _nav_action = {"type": "navigate", "target_hint": _nav_hint}
    actions.append(_nav_action)

    # ★ 通用修复(2026-07-20 v2): 注入侧边栏菜单点击 action
    #   当 target_page 对应的虚拟URL含 sidebar_text 时，navigate 后插入 click(sidebar_text)。
    #   原因: 某SPA系统侧边栏菜单点击后URL不变，Step4 navigate 到 real_url 后需点击 sidebar
    #   才能切换到目标"虚拟页面"（实际是Tab/组件状态切换）。
    #   通用性: 适用于所有"侧边栏菜单点击后URL不变"的SPA系统。
    #   合理性: 与 spa_menu.py 虚拟URL合成逻辑闭环。
    #   覆盖性: 兼容有 sidebar_text 和无 sidebar_text 两种场景。
    if _sidebar_click_text:
        actions.append({"type": "click", "target_hint": _sidebar_click_text})

    # ★ P0-fix(2026-07-24): §12.2 规则2 — DOM按钮列表为空时降级已在主流程(L1989-2019)做
    #   回滚 ADR-017 方案B: 原逻辑"不降级+intent:operation"违反 §12.1"Step4只执行不决策"
    #   此处仅记录 DOM 按钮列表状态，降级决策由主流程统一处理
    #   通用性: 适用于所有 _build_actions 调用路径
    #   覆盖性: 与主流程 L1989-2019 的降级逻辑形成双保险
    _DOM_BUTTONS_EMPTY = not dom_elements.get("button_texts")
    if _DOM_BUTTONS_EMPTY and control_type not in ():
        _log.warning(
            "[RuleExtract] §12.2规则2: 目标页面 '%s' DOM按钮列表为空 "
            "→ 降级应由主流程处理(若仍执行到此处说明主流程未拦截), operation=%r",
            target_page[:60],
            operation[:50],
        )

    # ★ Fix(2026-07-25): navigate-click 页面一致性校验（§12.2 规则2 增强）
    #   当 DOM 按钮非空但不包含 operation 关键词时，跨页面搜索正确页面并更新导航目标
    #   根因(§10.9 3层why):
    #     L1: element_exists:✗(找到0个匹配元素) → 导航到错误页面（如 dashboard）
    #     L2: 为什么导航到错误页面? → _match_page_path 返回 dashboard（无匹配按钮）
    #     L3: 为什么返回 dashboard? → nav_text 匹配失败 + min() 兜底选最短路径
    #   全局观(§13.3): Step3 职责是生成可执行用例，navigate 必须指向包含所需按钮的页面
    #   修复: 用 operation 中文关键词在所有 page_dom_snapshots 中搜索按钮所在页面
    #   通用性(§12.4):
    #     - 跨系统: 纯基于 page_dom_snapshots 查询，不依赖任何框架特性
    #     - 跨行业: 无业务关键词硬编码，仅用 operation 中文关键词匹配
    #     - 跨页面: 搜索所有页面（含 Tab 子页面），找到即返回
    #     - 数据完整性: 无匹配时保持原 navigate 目标，不崩溃
    #   覆盖性: 覆盖所有 control_type（input/textarea/select/upload/button/display）
    #   自我反驳3次:
    #     反驳1: 关键词匹配可能误匹配（如"上传"匹配到"上传记录"而非"上传模型"）
    #       → 取 operation 中最长的中文词组（2-4字），降低误匹配概率
    #     反驳2: 跨页面搜索增加 DB 查询开销
    #       → 仅在当前页面 DOM 按钮不匹配时触发（大多数情况不触发）
    #     反驳3: 应该直接修复 _match_page_path 而不是在这里打补丁
    #       → _match_page_path 修复是长期方案，但 _match_page_path 可能永远无法 100%
    #         覆盖所有场景（如 operation 语义模糊），此处是防御性校验，不可省略
    if not _DOM_BUTTONS_EMPTY and control_type not in ():
        _btn_texts = dom_elements.get("button_texts", [])
        # 提取 operation 中的中文关键词（2-4字词组）
        _cn_chars = re.findall(r"[\u4e00-\u9fff]", operation)
        _op_keywords = []
        for length in (4, 3, 2):
            for i in range(len(_cn_chars) - length + 1):
                _op_keywords.append("".join(_cn_chars[i : i + length]))
        # 检查是否有任一关键词在当前页面按钮中存在
        _has_match = (
            any(any(kw in btn for kw in _op_keywords) for btn in _btn_texts)
            if _op_keywords
            else True
        )
        if not _has_match and _op_keywords:
            _correct_page = _find_page_for_button(_op_keywords)
            if _correct_page and _correct_page != target_page:
                _log.info(
                    "[RuleExtract] ★ 页面一致性校验: op='%s' 按钮不在 '%s' → 重定向到 '%s'",
                    operation[:30],
                    target_page[:60],
                    _correct_page,
                )
                _nav_url = _correct_page
                target_page = _correct_page
                _nav_hint = _correct_page
                # 更新 navigate action（已在 actions[0]）
                actions[0] = {"type": "navigate", "target_hint": _correct_page}
                # 重新加载正确页面的 DOM 元素
                dom_elements = _load_dom_elements_for_page(_correct_page)
                _log.info(
                    "[RuleExtract] ★ 页面一致性校验: 已重定向到 '%s', 按钮=%s",
                    _correct_page,
                    dom_elements.get("button_texts", [])[:5],
                )

    # ★ 通用修复(2026-07-20): 注入菜单导航点击序列
    #   在 navigate 到首页后，逐级点击菜单导航到目标页面
    for _menu_action in _nav_menu_clicks:
        actions.append(_menu_action)

    # Step 2: 依据「操作意图 → 控件角色」推导并组装交互动作（单一实现，SSOT）
    #   ★ 根因修复(2026-09-27)：本函数原先自行维护关键词表并就地拼装动作序，与
    #     `_fallback_cases` 探针路径口径不一致——变更型操作（新增/编辑/删除/导入…）
    #     产出 `fill(先) → click(触发按钮)`：弹窗尚未打开就往表单里填，fill 目标必错、
    #     click 又去开了个空弹窗 → Step4 结构化执行必败 → 降级 AI 探索不收敛。
    #     现统一收敛到 `_rule_extractor_buttons.build_interaction_actions`：
    #       变更型（dialog_form）→ click(触发,in_dialog=False) → 录入(in_dialog=True)
    #                             → click(确认,in_dialog=True)
    #       只读/提交型（inline_form）→ 录入 → click(操作控件)
    #       删除型（trigger_confirm）→ click(触发) → [click(确认,in_dialog=True)]
    #   通用性(§1)：只依赖「动词→类别」SSOT + "本用例是否存在录入动作"这一结构事实，
    #     跨系统/行业/框架成立；不再保留任何局部关键词表（原 `_is_dialog_op` 漏
    #     "新增/添加/创建/编辑/更新" 就是口径漂移的实证）。
    _op_ids = {op["action_id"] for op in selected_ops}
    _dom_buttons = dom_elements["button_texts"]
    _trigger_text = _infer_click_target(operation, dom_buttons=_dom_buttons)
    _confirm_text = infer_confirm_hint(operation, _dom_buttons)

    _data_action = None
    # ★ Fix E 根因修复(2026-09-27)：变更型正向用例按需求字段规格**完整录入**。
    #   与 `_fallback_cases._build_base_actions` 共用同一 SSOT（`_rule_extractor_fields`）。
    #   根因：原先只产出单个 fill → 多必填字段的表单被客户端校验拦截 → 结构化执行
    #   必败 → 降级 AI 探索 8 轮不收敛（单条 6.4 分钟，既失真又低效）。
    #   关联不到字段规格时返回空 → 维持原有单 fill（诚实降级，不臆造字段）。
    if not error_case and not boundary_case:
        try:
            from src.pipeline.step3._rule_extractor_fields import (
                build_form_data_actions,
            )

            _field_actions = build_form_data_actions(
                operation,
                module=str(req.get("module", "") or ""),
                unique_value="",
            )
        except Exception as _fe:  # noqa: BLE001
            _log.debug(
                "[RuleExtract] 字段规格组装失败(降级单fill): %s", str(_fe)[:80]
            )
            _field_actions = []
        # 仅变更型（新增/编辑/导入…）多字段录入有意义；检索型/负向仍走下方单 fill
        if _field_actions and mutation_flow_kind(operation, True) == "dialog_form":
            _data_action = _field_actions

    if _data_action is None:
        if control_type in ("input", "textarea"):
            if "fill" in _op_ids:
                _fill_target, _fill_value = _infer_fill_target_and_value(
                    req,
                    rag_data,
                    error_case,
                    boundary_case,
                    dom_inputs=dom_elements["input_placeholders"],
                )
                _data_action = {
                    "type": "fill",
                    "target_hint": _fill_target,
                    "value": _fill_value,
                }
        elif control_type == "select":
            if "select_option" in _op_ids:
                _data_action = {
                    "type": "select_option",
                    "target_hint": "下拉选择框",
                    "option_text": _infer_select_option(req, rag_data, error_case),
                }
        elif control_type == "upload":
            if "upload_file" in _op_ids:
                # ★ 治本修复 (2026-07-17): 用 operation 文本的动作关键词代替泛化"文件上传控件"
                _data_action = {
                    "type": "upload_file",
                    "target_hint": _infer_submit_button_text(
                        operation, dom_buttons=_dom_buttons
                    ),
                    "file": "test_sample.csv",
                }
        else:  # button / display / default
            # 只读检索型（查询/搜索/筛选）需先录入检索词再触发操作控件
            if "fill" in _op_ids and classify_operation_category(operation) == "query":
                _fill_target, _fill_value = _infer_fill_target_and_value(
                    req,
                    rag_data,
                    error_case,
                    boundary_case,
                    dom_inputs=dom_elements["input_placeholders"],
                )
                _data_action = {
                    "type": "fill",
                    "target_hint": _fill_target,
                    "value": _fill_value,
                }

    actions.extend(
        build_interaction_actions(
            operation, _data_action, _trigger_text, _confirm_text
        )
    )

    # Step 3: screenshot（通用，用于 Step4 失败时定位）
    if "screenshot" in _op_ids:
        actions.append({"type": "screenshot", "target_hint": "操作后截图"})

    return actions


def _infer_fill_target_and_value(
    req: dict,
    rag_data: dict,
    error_case: bool,
    boundary_case: bool,
    dom_inputs: list[str] = None,
) -> tuple[str, str]:
    """推断 fill 的 target_hint 和 value（从 RAG API 参数提取，不编造）。

    ★ v2.0 (2026-07-07): target_hint 优先用 RAG API body_params 的字段名（更具体）
    ★ Fix47 (2026-07-09): 从 DOM 快照中匹配实际输入框 placeholder（更准确的定位）
      优先级：DOM placeholder > RAG 字段名 > operation 语义 > 通用兜底
    """
    operation = str(req.get("operation", "") or "").lower()
    dom_inputs = dom_inputs or []

    # Fix28d: 找第一个有 body_params 的 API; §13.3: 解析(字段名,类型)对供 generate_value_from_type
    _rag_first_field = ""
    _rag_first_type = ""
    _rag_api_with_body = None
    for api in rag_data.get("apis", []):
        body_params = api.get("body_params", "")
        if body_params:
            _field_pairs = re.findall(r"(\w+)\s*(?:\(([^)]*)\))?", body_params)
            if _field_pairs:
                _rag_first_field = _field_pairs[0][0].lower()
                _rag_first_type = (
                    _field_pairs[0][1] if len(_field_pairs[0]) > 1 else ""
                ).lower()
                _rag_api_with_body = api
                break

    # ★ Fix47: target_hint 四层优先级：
    #   1. DOM 实际 placeholder（如 "请搜索叶组名称"）— 最准确
    #   2. RAG API body_params 第一个字段名（如 "name 输入框"）— 次准确
    #   3. operation 语义推断（如 "查询输入框"、"名称输入框"）— 较具体
    #   4. 通用兜底 "输入框" — SelfHealing type_fallback
    target_hint = None
    if _rag_first_field and dom_inputs:
        target_hint = _match_input_by_field_name(_rag_first_field, dom_inputs)
    if not target_hint and _rag_first_field and len(_rag_first_field) >= 2:
        target_hint = f"{_rag_first_field} 输入框"
    if not target_hint:
        if any(k in operation for k in ["查询", "搜索", "筛选"]):
            # 搜索类：优先匹配 DOM 中的搜索输入框
            if dom_inputs:
                for inp in dom_inputs:
                    if any(k in inp for k in ["搜索", "查询", "search"]):
                        target_hint = inp
                        break
            if not target_hint:
                target_hint = "查询输入框"
        elif any(k in operation for k in ["名称", "名字", "标题"]):
            target_hint = "名称输入框"
        elif any(k in operation for k in ["编号", "代码", "code", "编码"]):
            target_hint = "编号输入框"
        elif any(k in operation for k in ["密码", "口令"]):
            target_hint = "密码输入框"
        elif any(k in operation for k in ["邮箱", "邮件", "email"]):
            target_hint = "邮箱输入框"
        elif any(k in operation for k in ["手机", "电话", "phone"]):
            target_hint = "手机号输入框"
        else:
            target_hint = "输入框"

    # value 选择 — ★ §13.3 覆盖性修复(2026-07-24): 无 RAG 时也走 value_generator(operation 语义推断)
    _op_type = (
        "integer"
        if any(k in operation for k in ["数量", "金额", "价格"])
        else (
            "boolean"
            if any(k in operation for k in ["是否", "启用", "禁用"])
            else "string"
        )
    )
    _vf, _vt = _rag_first_field or operation, _rag_first_type or _op_type
    if error_case:
        try:
            from src.pipeline.step3.value_generator import (
                generate_invalid_value_from_type as _giv,
            )

            return target_hint, _giv(_vt, _vf)
        except Exception:
            return target_hint, "!@#"
    if boundary_case:
        return target_hint, ""
    try:
        from src.pipeline.step3.value_generator import generate_value_from_type as _gv

        return target_hint, _gv(_vt, _vf)
    except Exception:
        return target_hint, "测试数据"


def _infer_select_option(req: dict, rag_data: dict, error_case: bool) -> str:
    """推断 select_option 的 option_text（通用，不编造业务选项）。"""
    if error_case:
        return "无效选项"
    return "第一项"


def _infer_submit_button_text(operation: str, dom_buttons: list[str] = None) -> str:
    """推断提交按钮文本。

    ★ Fix47 (2026-07-09): 优先从 DOM 中匹配实际按钮文本，兜底用通用语义。
    ★ 治本修复 (2026-07-17): 当 DOM 按钮为空时，用 operation 文本自身的动作关键词
      作为 hint（如"批量导入辅材"→"导入"），而非泛化"X按钮"。
      原因：泛化"导入按钮"与 某SPA系统 实际按钮文本"批量导入"不匹配，
      SelfHealing partial_text 策略用实际动作词"导入"匹配成功率更高。
      通用性：不硬编码业务词，纯基于 operation 文本的动作关键词提取。

    ★ Fix(2026-07-25): 触发按钮 vs 提交按钮区分（§13.3 职责边界）
      当 _match_button_by_keywords 匹配到触发按钮（如"修改配置"）时，
      优先在 DOM 中查找提交按钮（保存/确定/提交），因为 _infer_submit_button_text
      的语义是"提交按钮"，不是"触发按钮"。
      根因(§10.9 3层why):
        L1: N01 element_exists:✗ → 点击触发按钮后无错误提示
        L2: 为什么点击触发按钮? → _infer_submit_button_text 返回"修改配置"
        L3: 为什么返回"修改配置"? → _match_button_by_keywords 语义类别匹配("edit") 优先于提交语义
      通用性: 适用于所有 CRUD 系统（React/Vue/Angular/原生HTML）
      覆盖性: 影响所有 input/textarea/select/upload 的 submit 按钮生成
    """
    dom_buttons = dom_buttons or []
    if dom_buttons:
        matched = _match_button_by_keywords(operation, dom_buttons)
        if matched:
            # ★ Fix(2026-07-25): 触发按钮 vs 提交按钮区分
            #   若匹配到的按钮是触发按钮，且 DOM 中有提交按钮，优先用提交按钮
            _is_trigger = any(v in matched for v in _TRIGGER_VERBS)
            if _is_trigger:
                _submit_candidates = [
                    b for b in dom_buttons if any(v in b for v in _SUBMIT_VERBS)
                ]
                if _submit_candidates:
                    _log.info(
                        "[RuleExtract] ★ 触发→提交按钮切换: op='%s' matched='%s' → submit='%s'",
                        operation[:30],
                        matched,
                        _submit_candidates[0],
                    )
                    return _submit_candidates[0]
            return matched
    op = operation if operation else ""
    op_lower = op.lower()

    # 登录：保留"登录"（实际按钮通常就是"登录"）
    if any(k in op_lower for k in ["登录", "login"]):
        return "登录"
    # 查询/搜索/筛选 → "查询"（SelfHealing 能匹配"搜索"、"查询"等按钮）
    if any(k in op for k in ["查询", "搜索", "筛选"]):
        return "查询"
    # 新建/新增/添加/创建 → "新建"（而非"新建/提交按钮"）
    if any(k in op for k in ["新建", "新增", "添加", "创建"]):
        return "新建"
    # 删除/移除 → "删除"
    if any(k in op for k in ["删除", "移除"]):
        return "删除"
    # 编辑/修改/更新 → "保存"（编辑操作通常对应保存按钮）
    if any(k in op for k in ["编辑", "修改", "更新"]):
        return "保存"
    # 导出/下载 → "导出"
    if any(k in op for k in ["导出", "下载"]):
        return "导出"
    # 导入/上传 → "导入"（而非"导入按钮"；SelfHealing 能匹配"批量导入"等）
    if any(k in op for k in ["导入", "上传"]):
        return "导入"
    # 确定/确认/ok → "确定"
    if any(k in op_lower for k in ["确定", "确认", "ok"]):
        return "确定"
    # 取消/关闭 → "取消"
    if any(k in op for k in ["取消", "关闭"]):
        return "取消"

    # 通用兜底：取 operation 前4个中文字作为 hint
    # 大多数中文操作词 ≤ 4字（如"批量打标"、"提交审核"）
    # SelfHealing partial_text 能匹配实际按钮文本中的子串
    import re as _re_btn

    _cn_chars = _re_btn.findall(r"[\u4e00-\u9fff]", op)
    if _cn_chars:
        return "".join(_cn_chars[:4])
    return "提交"


def _infer_click_target(operation: str, dom_buttons: list[str] = None) -> str:
    """推断 click 的 target_hint。

    ★ Fix47 (2026-07-09): 优先从 DOM 中匹配实际按钮文本，兜底用通用语义。
    ★ 治本修复 : 复用 _infer_submit_button_text 的修复逻辑。
    """
    dom_buttons = dom_buttons or []
    if dom_buttons:
        matched = _match_button_by_keywords(operation, dom_buttons)
        if matched:
            return matched
    return _infer_submit_button_text(operation, [])


def _infer_required_tracks(
    operation: str, matched_apis: list[dict], rag_data: dict
) -> list[str]:
    """推断校验轨道（通用，基于 RAG 数据源可用性）。

    ★ Fix v2 (2026-07-14): 用资源可用性检测替代 operation 文本正则匹配。
      原因：Step2 重新拆解后 operation 是纯中文（如"用户请求获取叶组列表"），
      不含 "GET /path" 格式，正则检测失效。
      新策略：有 API 关联但无 DOM/页面资源 → API-only（无 UI 可测）。
      这对任何系统通用（API 接口需求天然无对应页面）。

    契约：§4.2 "通用性" — 不硬编码业务关键词，只看 RAG 资源结构
    """
    has_apis = bool(matched_apis or rag_data.get("apis"))
    has_dom = bool(rag_data.get("doms"))

    if has_apis and not has_dom:
        # ★ 治本修复 (2026-07-17): 有 API 但无 DOM 不一定就是 API-only。
        #   原逻辑：有 API 无 DOM → tracks=["api"]，导致 actions 清空，Step4 无法执行。
        #   问题：某SPA系统 等 SPA 应用中，需求有 API 回溯但 DOM 爬虫未抓取子页面，
        #         需求本身是 UI 操作（上传/输入/点击/搜索），不是纯 API 接口。
        #   修复：有 API 无 DOM 时，仍需包含 "ui" track，让 _build_actions 生成
        #         导航+操作步骤，nav_map 菜单导航策略做兜底。
        #   通用性：不硬编码业务词，基于 control_type 和 API 结构判断。
        #   契约：§4.2 "通用性" — 基于 RAG 资源结构判断
        tracks = ["api"]
        if rag_data.get("db_tables"):
            tracks.append("db")
        # 有 API 无 DOM 但需求本身是 UI 操作 → 仍需 UI track
        # Step4 的 menu_navigate 策略可以处理 SPA 菜单导航
        tracks.append("ui")
        return tracks

    # 有 UI（DOM）资源或无任何资源：走浏览器执行
    tracks = ["ui"]
    if has_apis:
        tracks.append("api")
    if rag_data.get("db_tables"):
        tracks.append("db")
    return tracks
