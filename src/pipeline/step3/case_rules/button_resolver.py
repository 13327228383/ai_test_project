# -*- coding: utf-8 -*-
"""按钮定位辅助函数（从 case_rules/base.py 拆分，§4.3 单文件≤800行）。

职责：按钮文本推断 + dropdown 两步操作生成 + 跨页面按钮搜索。
逻辑不变，仅结构重构，遵循 step2_helpers.py 拆分模式。

被以下文件调用（通过 BaseCaseRule 类属性赋值，零调用方改动）：
  - case_rules/base.py（内部调用）
  - case_rules/{input,button,form,date,select,radio,checkbox,upload}_rule.py
  - _rule_extractor.py / _control_validation.py（间接）
"""

import logging
from src.core.guarded import guard
from typing import Any


# ── 按钮/触发词表唯一源（下游 import 引用，勿重抄）──
_TRIGGER_VERBS = {
    "新建", "新增", "添加", "创建", "修改", "编辑", "更新", "删除", "移除",
}
_SUBMIT_VERBS = {"保存", "提交", "确定", "确认"}

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 按钮文本推断（intent: 语义意图架构）
# ═══════════════════════════════════════════════════════════════


def _infer_submit_text(operation: str, target_page: str = "", merged_data: dict = None):
    """推断提交按钮文本。

     优先级：DOM button_texts（按 operation 关键词匹配）> None（触发降级）

     ★ P0-fix(2026-07-25): 完全回滚 ADR-017 方案B（intent:语义意图）
       根因核查(§10.9):
         1. 现象: DOM按钮非空但无匹配时返回 intent:{operation}，case_rules 降级判断永不触发
         2. 直接原因: _infer_submit_text 返回 intent: 而非 None
         3. 中间原因: ADR-017 方案B 未完全回滚（P0-fix 只回滚主流程，漏回滚 button_resolver）
         4. 根本原因: ADR-017 与 §12.2规则2 冲突；intent: 让 Step4 实时找按钮违反 §12.1
       全局观: 影响所有 case_rules + _fallback_cases + _build_actions
       职责边界: Step3 用例建模职责内（§13.3）；"找按钮"是 Step3 职责，Step4 只执行（§12.1）
    #   通用性(§12.4): 跨系统/跨行业/跨页面 — 所有"DOM按钮无匹配"场景返回 None，降级=失败
       自我反驳三次:
         1) 保留intent:? → 违反§12.1，Step4做决策；且 case_rules 降级判断永不触发
         2) 跨页面搜索后仍无匹配? → _resolve_button_cross_page 已在 case_rules 中单独处理
         3) 返回None → 符合§12.2规则2，触发 fallback 到下一个规则或 _fallback_cases，最优
    #   合规: §12.2规则2 — click target_hint 必须在 DOM 按钮列表中存在，否则返回 None（降级=失败）
    """
    op = (operation or "").lower()
    _op_raw = (operation or "").strip()

    # ★ Fix49: 优先从 DOM 快照匹配实际按钮文本
    if target_page:
        try:
            from src.pipeline.step3._rule_extractor import (
                _load_dom_elements_for_page,
                _match_button_by_keywords,
            )

            dom_elements = _load_dom_elements_for_page(target_page)
            buttons = dom_elements.get("button_texts", [])
            if buttons:
                matched = _match_button_by_keywords(operation, buttons)
                if matched:
                    _is_trigger = any(v in matched for v in _TRIGGER_VERBS)
                    if _is_trigger:
                        _submit_candidates = [
                            b for b in buttons if any(v in b for v in _SUBMIT_VERBS)
                        ]
                        if _submit_candidates:
                            _log.info(
                                "[_infer_submit_text] ★ 触发→提交按钮切换: op='%s' matched='%s' → submit='%s'",
                                operation[:30],
                                matched,
                                _submit_candidates[0],
                            )
                            return _submit_candidates[0]
                        # ★ DOM无提交按钮 → 用通用语义兜底（与 _infer_submit_button_text 保持一致）
                        #   编辑/修改/更新 → "保存"；新建/新增/添加/创建 → "新建"（但新建通常也是保存）
                        #   通用性: 中文 CRUD 系统最常见的提交按钮文本
                        _fallback = "保存"
                        if any(v in matched for v in {"新建", "新增", "添加", "创建"}):
                            _fallback = "新建"
                        _log.info(
                            "[_infer_submit_text] ★ 触发按钮无提交按钮: op='%s' matched='%s' → fallback='%s'",
                            operation[:30],
                            matched,
                            _fallback,
                        )
                        return _fallback
                    return matched
                _log.info(
                    "[_infer_submit_text] DOM有按钮无匹配: operation=%r → 返回None触发降级(§12.2规则2)",
                    operation[:50],
                )
                return None
            _log.info(
                "[_infer_submit_text] DOM无按钮数据: operation=%r → 返回None触发降级(§12.2规则2)",
                operation[:50],
            )
            return None
        except Exception as _e:
            _log.debug("DOM按钮文本匹配失败: %s → 返回None触发降级", _e, exc_info=True)
            return None

    # ★ 无 target_page 时返回 None（无法校验 DOM，触发降级）
    return None


# ═══════════════════════════════════════════════════════════════
# dropdown-trigger 两步 action 生成（ARIA 契约驱动）
# ═══════════════════════════════════════════════════════════════


def _build_click_actions(
    operation: str, target_page: str, merged_data: dict = None
) -> list:
    """★ v16.1 通用修复(2026-07-23): 生成 click action 列表,自动处理 dropdown-trigger 两步操作。

    根因核查(§10.9):
      1. 现象: "数据导入"是 dropdown-trigger,需两步(展开+选菜单项),但 Step3 只生成一步 click
      2. 直接原因: Step3 不知"数据导入"是 dropdown-trigger
      3. 中间原因: Step1 未爬取 dropdown 菜单项
      4. 根本原因: 缺少 ARIA 契约数据 → Step4 用 v12/v13/v15 diff+猜(补丁堆叠)
    v16.1 修正(本版): v16 依赖 _infer_submit_text 先返回 trigger 文本,但 dropdown-trigger
      在 dropdown_triggers 字段不在 button_texts → _infer_submit_text 返回 intent: →
      _match_dropdown_item 见 intent: 直接返回 "" → v16 两步形同虚设。
      修正: _build_click_actions 优先用 operation 直接匹配 dropdown_triggers 的 trigger,
      不依赖 _infer_submit_text。
    通用性(§12.4): 基于 Step1 ARIA 契约数据(dropdown_triggers)+2-gram 语义匹配,不依赖框架类名
    全局观: Step3 声明两步 → Step4 精确执行(删除 v12/v13/v15) → 消除补丁堆叠
    职责边界: Step3 用例建模职责内(§13.3)
    自我反驳:
      1) 漏洞? trigger 2-gram 误匹配 → 取最高分,并列取第一个,Step4 失败回传 actual items
      2) 更通用? Step4 实时读 dropdown? → 违反 §12.1 Step4 只执行不决策
      3) 更根本? Step1 把 dropdown-trigger 加入 button_texts? → 数据污染,trigger 非普通按钮
    """
    # v16.1: 优先用 operation 匹配 dropdown_triggers 的 trigger 文本
    _dd_trigger_text = _match_dropdown_trigger_by_semantic(operation, target_page)
    if _dd_trigger_text:
        _dd_item = _match_dropdown_item(operation, target_page, _dd_trigger_text)
        if _dd_item:
            _log.info(
                "[_build_click_actions] v16.1 dropdown 两步: '%s' → '%s' (op=%r)",
                _dd_trigger_text,
                _dd_item,
                operation[:30],
            )
            return [
                {"type": "click", "target_hint": _dd_trigger_text},
                {"type": "click", "target_hint": _dd_item},
            ]
        # ★ P0-fix(2026-07-24): 回滚 ADR-017 intent: 前缀 — 用 operation 原始文本
        #   原: intent:{operation} 让 Step4 实时找 → 违反 §12.1 Step4 只执行不决策
        #   修复: 用 operation 原始文本，Step4 常规 click 找，找不到诚实报错
        #   通用性: 适用于所有 dropdown 菜单项无匹配场景
        _log.info(
            "[_build_click_actions] v16.1 dropdown 两步(trigger匹配,菜单项用operation原文): '%s' (op=%r)",
            _dd_trigger_text,
            operation[:30],
        )
        return [
            {"type": "click", "target_hint": _dd_trigger_text},
            {"type": "click", "target_hint": operation},
        ]
    # 非 dropdown → 走原逻辑(单步)
    _submit = _infer_submit_text(operation, target_page, merged_data)
    if not _submit:
        return []
    _dd_item = _match_dropdown_item(operation, target_page, _submit)
    if _dd_item:
        return [
            {"type": "click", "target_hint": _submit},
            {"type": "click", "target_hint": _dd_item},
        ]
    return [{"type": "click", "target_hint": _submit}]


def _match_dropdown_trigger_by_semantic(operation: str, target_page: str) -> str:
    """v16.1: 用 operation 2-gram 匹配 dropdown_triggers 的 trigger 文本。

    返回匹配的 trigger 文本或空。复用 _match_dropdown_item_by_semantic(2-gram 通用匹配)。
    通用性: 2-gram 不依赖业务词,跨系统/行业通用。
    """
    if not target_page or not operation:
        return ""
    try:
        from src.pipeline.step3._rule_extractor import _load_dom_elements_for_page

        _dom = _load_dom_elements_for_page(target_page)
        _triggers = [
            _dd.get("trigger", "") for _dd in _dom.get("dropdown_triggers", [])
        ]
        _triggers = [t for t in _triggers if t]
        if not _triggers:
            return ""
        return _match_dropdown_item_by_semantic(operation, _triggers)
    except Exception as _e:
        _log.debug("[_match_dropdown_trigger_by_semantic] 异常: %s", _e, exc_info=True)
    return ""


def _match_dropdown_item(operation: str, target_page: str, trigger_text: str) -> str:
    """v16: 若 trigger_text 是 dropdown-trigger,用 operation 语义匹配菜单项文本。

    返回菜单项文本;非 dropdown 或无匹配返回 ""。
    通用性: 基于 Step1 ARIA 契约(dropdown_triggers),不依赖框架类名。
    """
    if not target_page or not trigger_text:
        return ""
    if trigger_text.startswith("intent:"):
        return ""  # intent: 兜底,无 DOM 数据无法判定 dropdown
    try:
        from src.pipeline.step3._rule_extractor import _load_dom_elements_for_page

        _dom = _load_dom_elements_for_page(target_page)
        for _dd in _dom.get("dropdown_triggers", []):
            if _dd.get("trigger", "") == trigger_text:
                _items = _dd.get("items", []) or []
                _matched = _match_dropdown_item_by_semantic(operation, _items)
                if _matched:
                    _log.info(
                        "[_match_dropdown_item] dropdown '%s' → 菜单项 '%s' (op=%r)",
                        trigger_text,
                        _matched,
                        operation[:30],
                    )
                return _matched
    except Exception as _e:
        _log.debug("[_match_dropdown_item] 异常: %s", _e, exc_info=True)
    return ""


def _match_dropdown_item_by_semantic(operation: str, items: list) -> str:
    """v16: 用 operation 的 2-gram 字符匹配从 dropdown 菜单项中选最佳。

    通用性: 2-gram 字符匹配不依赖业务词,跨系统/行业通用。
    歧义处理: 并列最高分取第一个(Step1 爬取顺序为默认),Step4 精确点击失败则回传。
    """
    if not items or not operation:
        return ""
    _op = operation.strip()
    _scored = []
    for _item in items:
        _score = 0
        for _i in range(len(_op) - 1):
            if _op[_i : _i + 2] in _item:
                _score += 1
        _scored.append((_score, _item))
    _scored.sort(key=lambda x: -x[0])
    if not _scored or _scored[0][0] < 1:
        return ""
    return _scored[0][1]


# ═══════════════════════════════════════════════════════════════
# 跨页面按钮搜索（v3.9 通用修复）
# ═══════════════════════════════════════════════════════════════


def _resolve_button_cross_page(operation: str, target_page: str) -> tuple:
    """★ 通用修复(2026-07-22): 跨页面搜索按钮，返回 (button_text, page_path) 或 (None, None)。

    根因: _infer_submit_text 只搜索 target_page，当按钮在其他 Tab 页面上时返回 None → 降级。
          但降级用例只做页面可访问性验证，不做业务测试，覆盖率低下。

    修复: 当 target_page 找不到按钮时，搜索所有页面的按钮列表。
          找到后返回 (button_text, page_path)，调用方更新 target_page 为按钮所在页面。

    通用性:
    - 跨系统: 适用于 React/Vue/Angular/原生 HTML
    - 跨行业: 适用于电商/SaaS/ERP/配方系统
    - 跨页面: 适用于列表页/详情页/表单页/Tab页
    - 覆盖性: 影响所有调用 _infer_submit_text 的规则

    数据源优先级:
    1. nav_graph_nodes.page_elements.buttons（结构化真实爬取，最完整）
    2. nav_graph 合成 dom_summary（补充遗漏按钮）
    3. rag_chunks（最细粒度兜底）

    匹配策略:
    - 用 _match_button_by_keywords 做语义匹配（不硬编码按钮名）
    - 优先匹配同 base_path 的页面（兄弟 Tab 页面）
    - 其次匹配其他页面

    合规: AGENTS.md §12.4 通用性自检 — 跨页面通用性
    """
    if not operation or not target_page:
        return (None, None)

    try:
        from src.pipeline.step3._rule_extractor import _match_button_by_keywords
        from src.core.config_loader import config
        from src.core.db_manager import get_connection
        import json as _json
        from urllib.parse import urlparse
        import re as _re

        project = getattr(config, "project_name", "") or ""
        if not project:
            return (None, None)

        # target_page 的 base_path（用于优先级排序）
        _target_base = target_page.split("?")[0] if "?" in target_page else target_page
        if _target_base.startswith("http"):
            with guard("_resolve_button_cross_page: _target_base = urlparse(_target_base", log=_log):
                _target_base = urlparse(_target_base).path or "/"

        conn = get_connection()
        try:
            # ★ v5.0.7 修复(2026-07-27, §10.9): _collect_nav_map_candidates 是 in-place 修改，
            #   不是返回 _candidates。原代码 `_candidates = _collect_nav_map_candidates(...)`
            #   导致 _candidates=None，后续 _collect_dom_snapshot_candidates 收到 None 报错。
            #   根因(§10.9 3层why):
            #     L1: REQ-017/018 upload_rule/input_rule 降级
            #     L2: _resolve_button_cross_page 异常 "_collect_nav_map_candidates() missing 1 required positional argument: '_candidates'"
            #     L3: L292 调用时 _candidates = _collect_nav_map_candidates(...) 误以为有返回值，
            #         但函数签名是 (conn, project, target_page, _target_base, _candidates) -> None
            #   修复: 先创建 _candidates=[]，三个 _collect_* 函数都 in-place 追加
            #   通用性(§12.4): 跨系统/跨行业/跨页面通用（纯参数传递修复，无业务依赖）
            #   覆盖性: 影响所有 case_rules 的跨页面搜索（upload/input/button/form/...）
            #   职责边界: Step3 内部修复
            _candidates: list = []
            _collect_nav_map_candidates(
                conn, project, target_page, _target_base, _candidates
            )
            _collect_dom_snapshot_candidates(
                conn, project, target_page, _target_base, _candidates
            )
            _collect_rag_chunks_candidates(
                conn, project, target_page, _target_base, _candidates
            )
        finally:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[button_resolver] 操作异常: %s", _e, exc_info=True)

        if not _candidates:
            return (None, None)

        return _match_candidates_by_priority(operation, target_page, _candidates)
    except Exception as _e:
        _log.error(
            "[_resolve_button_cross_page] 跨页面搜索异常 operation=%r target_page=%r: %s",
            operation,
            target_page,
            _e,
            exc_info=True,
        )
        return (None, None)


def _collect_nav_map_candidates(
    conn, project: str, target_page: str, _target_base: str, _candidates: list
) -> None:
    """数据源1: nav_graph_nodes.page_elements.buttons（结构化真实爬取元素）。

    从导航图收集候选页面按钮，追加到 _candidates 列表。
    每个候选格式: (priority, page_path, button_texts, context_text)

    ★ P0-1 读侧迁移(2026-08-05): system_nav_maps.nav_json → nav_graph_nodes。
      根因: system_nav_maps 无 nav_json 列（实际 nav_map_json）且 0 行，
      原查询恒抛 Unknown column，被上层 except 吞掉 → 数据源1 永久失效。
    参数 conn 保留仅为兼容调用方签名（本函数已不直接用连接）。
    """
    from urllib.parse import urlparse
    from src.pipeline.utils.nav_graph_read import read_flat_list

    _flat = read_flat_list(project)
    if not _flat:
        _log.warning(
            "[button_resolver] nav_graph 无导航数据 project=%s，数据源1 跳过", project
        )
        return

    for _item in _flat:
        if not isinstance(_item, dict):
            continue
        _item_path = _item.get("path", "") or _item.get("page_path", "") or ""
        if not _item_path or _item_path == target_page:
            continue  # 跳过 target_page（已搜索过）
        _pe = _item.get("page_elements", {})
        if not _pe or not isinstance(_pe, dict):
            continue
        _buttons = _pe.get("buttons", [])
        if not _buttons:
            continue
        # 提取按钮文本
        _btn_texts = []
        for _btn in _buttons:
            if isinstance(_btn, dict):
                _t = (_btn.get("text", "") or "").strip()
            elif isinstance(_btn, str):
                _t = _btn.strip()
            else:
                _t = ""
            if _t:
                _btn_texts.append(_t)
        if not _btn_texts:
            continue
        # 优先级: 同 base_path > 其他
        _item_base = _item_path.split("?")[0] if "?" in _item_path else _item_path
        if _item_base.startswith("http"):
            try:
                _item_base = urlparse(_item_base).path or "/"
            except Exception as _e:
                _log.debug("[button_resolver] 操作异常: %s", _e, exc_info=True)
        _priority = 2 if _item_base == _target_base else 1
        # ★ v26: 构建页面语境文本（页面名+按钮+输入框placeholder/label+链接），供语义闸门校验
        _ctx_parts = [str(_item.get("text", "") or "")]
        for _inp in _pe.get("inputs") or []:
            if isinstance(_inp, dict):
                _ctx_parts.append(str(_inp.get("placeholder", "") or ""))
                _ctx_parts.append(str(_inp.get("label", "") or ""))
        for _lnk in _pe.get("links") or []:
            if isinstance(_lnk, dict):
                _ctx_parts.append(str(_lnk.get("text", "") or ""))
            elif isinstance(_lnk, str):
                _ctx_parts.append(_lnk)
        _ctx_parts.extend(_btn_texts)
        _ctx_text = " ".join(p for p in _ctx_parts if p)
        _candidates.append((_priority, _item_path, _btn_texts, _ctx_text))


def _collect_dom_snapshot_candidates(
    conn, project: str, target_page: str, _target_base: str, _candidates: list
) -> None:
    """数据源2: nav_graph 合成 DOM 摘要（补充数据源1 可能遗漏的按钮）。

    ★ P0-1 读侧迁移(2026-08-05): page_dom_snapshots(0 行) → nav_graph_nodes，
      dom_summary 由结构化 page_elements 合成，解析契约不变。
    参数 conn 保留仅为兼容调用方签名。
    """
    from src.pipeline.step3._rule_extractor import _parse_page_elements_line
    from src.pipeline.utils.nav_graph_read import read_dom_pages

    _dom_rows = read_dom_pages(project)

    for _row in _dom_rows:
        _item_path = _row.get("page_path", "") or ""
        _summary = _row.get("dom_summary", "") or ""
        # 原 SQL 过滤条件等价下推：element_count>0 且 page_path 不以 target_page 开头
        if not _item_path or not _summary:
            continue
        if int(_row.get("element_count") or 0) <= 0:
            continue
        if target_page and _item_path.startswith(target_page):
            continue
        _pe_result = _parse_page_elements_line(_summary)
        _btn_texts = _pe_result.get("button_texts", [])
        if not _btn_texts:
            continue
        _item_base = _item_path.split("?")[0] if "?" in _item_path else _item_path
        _priority = 2 if _item_base == _target_base else 1
        # ★ v26b: 语境只用 按钮+输入框placeholder+链接（★排除 TABS 段：
        #   Tab 标签是"兄弟页面的名字"，不是本页业务对象，混入会导致语义闸门失效，
        #   如叶组页 DOM 含「辅材配方」Tab 标签 → 辅材对象被错误放行）
        _ctx_text = " ".join(
            _pe_result.get("button_texts", [])
            + _pe_result.get("input_placeholders", [])
            + _pe_result.get("link_texts", [])
        )
        _candidates.append((_priority, _item_path, _btn_texts, _ctx_text))


def _collect_rag_chunks_candidates(
    conn, project: str, target_page: str, _target_base: str, _candidates: list
) -> None:
    """数据源3: rag_chunks（DOM 元素逐条存储，最细粒度兜底）。

    根因(3层追问):
      1. 表层: nav_map page_elements.buttons 和 page_dom_snapshots 对 Tab 页面多为空
      2. 中层: Step1 爬取 Tab 页面时，DOM 元素逐条存入 rag_chunks（含按钮），
               但未回填 nav_map page_elements → nav_map 数据不完整
      3. 根因: rag_chunks 是 Step1 爬取的最细粒度 DOM 数据源（每条 chunk = 一个元素），
               数据最完整；_resolve_button_cross_page 必须将其作为兜底数据源
    通用性:
      - 跨系统: DOM 元素格式 "[N] tag=xxx | text=yyy" 是 Step1 通用爬取格式
      - 跨行业: 不含任何业务关键词，纯结构化解析
      - 跨页面: Tab 页面/非 Tab 页面均适用
      - 覆盖性: 影响所有调用 _resolve_button_cross_page 的规则
    格式: text="[页面:标题] 路径:page_path\n元素:[N] tag=button | text=btn_text"
          meta=JSON{page_path, element_line, page_url, page_title}
    """
    import json as _json
    import re as _re_rag

    _rag_rows = (
        conn.execute(
            "SELECT chunk_id, text, meta FROM rag_chunks "
            "WHERE scope_type='project' AND scope_id=%s AND type='dom' "
            "AND text LIKE %s AND text LIKE %s",
            (project, "%tag=button%", "%| text=%"),
        ).fetchall()
        or []
    )

    # 按页面分组按钮（去重）
    _rag_btn_by_page: dict[str, list[str]] = {}
    _rag_ctx_by_page: dict[str, list[str]] = {}
    _re_btn = _re_rag.compile(r"tag=button\s*\|\s*text=([^\n|]+)")
    _re_path = _re_rag.compile(r"路径:(.+?)(?:\n|$)")
    for _row in _rag_rows:
        _text = _row.get("text", "") or ""
        if "tag=button" not in _text:
            continue
        # 提取 page_path: 优先 meta.page_path，回退 text 中 "路径:xxx"
        _page_path = ""
        _meta_raw = _row.get("meta", "")
        if _meta_raw:
            try:
                _meta_obj = (
                    _json.loads(_meta_raw) if isinstance(_meta_raw, str) else _meta_raw
                )
                _page_path = (
                    (_meta_obj.get("page_path", "") or "").strip()
                    if isinstance(_meta_obj, dict)
                    else ""
                )
            except Exception as _e:
                _log.debug("[button_resolver] 操作异常: %s", _e, exc_info=True)
        if not _page_path:
            _m_path = _re_path.search(_text)
            if _m_path:
                _page_path = _m_path.group(1).strip()
        if not _page_path or _page_path == target_page:
            continue  # 跳过空路径和已搜索的 target_page
        # 提取按钮文本（一条 chunk 可能含多个 button 元素）
        for _m_btn in _re_btn.finditer(_text):
            _btn_t = _m_btn.group(1).strip()
            if _btn_t and _btn_t not in _rag_btn_by_page.get(_page_path, []):
                _rag_btn_by_page.setdefault(_page_path, []).append(_btn_t)
                _rag_ctx_by_page.setdefault(_page_path, []).append(_btn_t)

    # 构建 candidates（与数据源1/2 同格式）
    for _item_path, _btn_texts in _rag_btn_by_page.items():
        if not _btn_texts:
            continue
        _item_base = _item_path.split("?")[0] if "?" in _item_path else _item_path
        _priority = 2 if _item_base == _target_base else 1
        _ctx_text = " ".join(_rag_ctx_by_page.get(_item_path, _btn_texts))
        _candidates.append((_priority, _item_path, _btn_texts, _ctx_text))

    if _rag_btn_by_page:
        _log.info(
            "[_resolve_button_cross_page] 数据源3(rag_chunks) 补充 %d 个页面的按钮数据",
            len(_rag_btn_by_page),
        )


def _match_candidates_by_priority(
    operation: str, target_page: str, _candidates: list
) -> tuple:
    """按优先级降序排序候选页面并逐页匹配按钮，含语义闸门校验。

    返回 (button_text, page_path) 或 (None, None)。
    """
    from src.pipeline.step3._rule_extractor import (
        _match_button_by_keywords,
        _operation_object_matches_page,
    )

    # 按优先级降序排序
    _candidates.sort(key=lambda x: x[0], reverse=True)

    # 逐页面匹配
    for _prio, _page_path, _btn_texts, _ctx_text in _candidates:
        _matched = _match_button_by_keywords(operation, _btn_texts)
        if _matched:
            # ★ v26: 语义闸门 — 按钮文本命中后，还必须通过"操作对象↔页面语境"一致性校验
            #   根因: REQ-002「批量导入辅材」命中叶组页「数据导入」按钮，但服务对象是叶组数据
            #   原则: 无法证伪则允许（对象词为空/语境为空），可证伪则拒绝（张冠李戴）
            if not _operation_object_matches_page(operation, _ctx_text):
                _log.warning(
                    "[_resolve_button_cross_page] 语义闸门拦截: operation=%r 的对象与页面 %r 语境不一致，"
                    "跳过该候选（按钮 %r 文本命中但语义不符）",
                    operation[:30],
                    _page_path[:60],
                    _matched,
                )
                continue
            _log.info(
                "[_resolve_button_cross_page] 跨页面匹配成功: "
                "operation=%r, target_page=%r → button=%r, found_page=%r (priority=%d)",
                operation[:30],
                target_page[:50],
                _matched,
                _page_path[:50],
                _prio,
            )
            return (_matched, _page_path)

    _log.warning(
        "[_resolve_button_cross_page] 跨页面搜索无匹配: operation=%r, target_page=%r, "
        "candidates=%d pages",
        operation[:30],
        target_page[:50],
        len(_candidates),
    )
    return (None, None)


