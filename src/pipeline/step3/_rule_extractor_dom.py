# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""规则提取 - DOM 元素加载与搜索关键词提取模块
================================================
★ 架构债清理(2026-07-25): 从 _rule_extractor.py 拆出（§4.3 单文件≤800行）。

职责：
  1. _load_nav_flat_list: 加载导航 flat_list（优先 nav_graph_nodes；原 system_nav_maps 降级分支已于 2026-08-05 DROP 移除）
  2. _load_dom_elements_for_page: 从 nav_graph_nodes（原 page_dom_snapshots）+ rag_chunks 加载 DOM 元素
  3. _extract_searchable_data_from_dom: 从 DOM 提取真实搜索关键词
通用性（§4.2）：基于 nav_graph_nodes + rag_chunks 查询，无业务关键词硬编码。
"""

import json
import logging
import re
from typing import Any

_log = logging.getLogger(__name__)


def _nav_dom_rows_by_prefix(project: str, path_prefix: str) -> list[dict[str, Any]]:
    """按 page_path 前缀取导航页面的 DOM 摘要行（按元素数降序）。

    ★ P0-1 读侧迁移(2026-08-05): 等价替换原 SQL —
        SELECT dom_summary FROM page_dom_snapshots
        WHERE project=%s AND page_path LIKE '<prefix>%' AND element_count > 0
        ORDER BY element_count DESC
      page_dom_snapshots 已 0 行且**全仓无任何写入方**，该 SQL 恒返回空，
      导致所有依赖它的按钮/输入框提取静默退化为"无 DOM 数据"。
      现改读 nav_graph_nodes（Step1 真实爬取产物），过滤/排序语义保持一致。

    Args:
        project: 项目名（为空直接返回空列表）。
        path_prefix: page_path 前缀（对应原 SQL 的 LIKE 'prefix%'）。

    Returns:
        [{page_path, dom_summary, element_count}, ...]，按 element_count 降序。
    """
    if not project or not path_prefix:
        return []
    from src.pipeline.utils.nav_graph_read import read_dom_pages

    try:
        pages = read_dom_pages(project)
    except Exception as exc:
        _log.error(
            "[RuleExtract] 导航页面读取失败 project=%s prefix=%r: %s",
            project,
            path_prefix,
            exc,
            exc_info=True,
        )
        return []

    matched = [
        {
            "page_path": str(p.get("page_path", "") or ""),
            "dom_summary": str(p.get("dom_summary", "") or ""),
            "element_count": int(p.get("element_count") or 0),
        }
        for p in pages
        if int(p.get("element_count") or 0) > 0
        and str(p.get("page_path", "") or "").startswith(path_prefix)
    ]
    matched.sort(key=lambda r: r["element_count"], reverse=True)
    return matched


def _load_nav_flat_list(project: str, version_label: str) -> list[dict]:
    """加载导航 flat_list（nav_graph_nodes 唯一数据源）。

    返回统一格式 [{path, page_elements}, ...]。

    ★ P0-1 读侧收口(2026-08-05):
      1) 删除 system_nav_maps 降级分支 —— 该表 0 行且**无 nav_json 列**，
         `SELECT nav_json` 恒抛 Unknown column 并被 except 吞掉，是纯死代码
         （AGENTS.md §10.5 零兼容负担：废弃表不保留兼容读取）。
      2) 改用 nav_graph_read 适配层：原实现要求 version_label 非空，否则直接
         跳过 nav_graph 走死降级 → 调用方未传版本时永远拿不到元素。
         适配层会自动解析该 project 最新版本，修掉这个断链。
      3) 读取失败改为 error 级详细日志（区分"无数据"与"读失败"）。
    """
    if not project:
        return []

    from src.pipeline.utils.nav_graph_read import read_flat_list

    try:
        _nodes = read_flat_list(project, version_label or None)
    except Exception as _e:
        _log.error(
            "[RuleExtract] nav_graph 读取失败 project=%s ver=%s: %s",
            project,
            version_label,
            _e,
            exc_info=True,
        )
        return []

    _flat: list[dict] = []
    for n in _nodes:
        _pe = n.get("page_elements")
        if not _pe or not isinstance(_pe, dict):
            continue
        _flat.append(
            {
                "path": n.get("path", "") or n.get("url", ""),
                "page_elements": _pe,
            }
        )

    if _flat:
        _log.debug(
            "[RuleExtract] nav_graph 命中: %d 节点 (project=%s, ver=%s)",
            len(_flat),
            project,
            version_label,
        )
    else:
        _log.warning(
            "[RuleExtract] nav_graph 无带元素的节点 project=%s ver=%s（DOM 元素将缺失）",
            project,
            version_label,
        )
    return _flat


def _load_dom_elements_for_page(page_path: str) -> dict[str, list[str]]:
    """★ Fix47 (2026-07-09): 从 page_dom_snapshots 表加载目标页面的 DOM 元素。
    ★ 通用修复(2026-07-20): 优先解析 [PAGE_ELEMENTS] 行 + rag_chunks 三级回退查询

    解析 dom_summary 文本，提取：
    - button_texts: 所有 button 标签的可见文本（用于 click target_hint 匹配）
    - input_placeholders: 所有 input/textarea 的 placeholder（用于 fill target_hint 匹配）
    - link_texts: 所有 a 标签的可见文本（用于 click target_hint 匹配）

    策略（通用，无业务硬编码）：
    1. 用 page_path 前缀匹配 page_dom_snapshots（LIKE），因为实际 URL 可能包含额外 query 参数
    2. 优先解析 dom_summary 的 [PAGE_ELEMENTS] 行（Step1 爬取时汇总的真实按钮列表）
       - 原因: [N] tag=button 没有 text 属性（Ant Design 的 button 用 span 嵌套），导致按钮列表为空
       - [PAGE_ELEMENTS] 行包含 Step1 爬取时汇总的真实按钮列表，适用于所有系统/行业/页面/Tab
    3. 回退到解析 "[N] tag=xxx | text=yyy | placeholder=zzz" 格式（向后兼容）
    4. 从 rag_chunks 补充：三级回退查询
       - 第一级: tab 参数精确匹配（避免跨 Tab 污染）
       - 第二级: base_path 匹配（兼容 URL 参数不一致的场景，如中英文 key 混用）
       - 第三级: 无 tab 参数时用 base_path 匹配（向后兼容）
    5. rag_chunks 多条记录时按 active Tab 匹配优先排序
    6. 解析 rag_chunks 的 [PAGE_ELEMENTS] 行 + [N] tag=xxx 格式
    7. 去重后返回

    返回: {"button_texts": [...], "input_placeholders": [...], "link_texts": [...], "dropdown_triggers": [{"trigger": str, "items": [str]}]}
    """
    # 延迟导入避免循环依赖：_parse_page_elements_line/_parse_dom_element_line 已拆至 _rule_extractor_api
    from ._rule_extractor_api import _parse_page_elements_line, _parse_dom_element_line

    result: dict[str, list] = {
        "button_texts": [],
        "input_placeholders": [],
        "link_texts": [],
        "dropdown_triggers": [],
    }
    if not page_path or page_path == "/login":
        return result

    # ★ 通用修复(2026-07-20 v4): 处理绝对 URL（nav_map real_url 是绝对URL）
    #   原bug: nav_map 虚拟URL的 real_url 是 http://<target-host>:<port>/dataManage/portraitData，
    #         而 page_dom_snapshots.page_path 是 /dataManage/portraitData（相对路径），
    #         LIKE 'http://...%' 永远不匹配，导致按钮列表为空，所有用例降级。
    #   通用性: 适用于所有系统的绝对URL（http/https），提取 path 部分用于匹配。
    #   合理性: 追根溯源到URL格式不一致的缺陷，page_dom_snapshots 只存相对路径。
    #   覆盖性: 兼容绝对URL和相对路径两种输入。
    if page_path.startswith("http://") or page_path.startswith("https://"):
        try:
            from urllib.parse import urlparse

            _parsed = urlparse(page_path)
            page_path = _parsed.path or "/"
            _log.debug(
                "[RuleExtract] _load_dom_elements_for_page: 绝对URL→相对路径 '%s'",
                page_path,
            )
        except ValueError as _rx2:
            # URL 解析失败 → 保留原始 page_path 继续匹配（不影响后续逻辑）
            _log.warning(
                "[RuleExtract] URL 解析失败，沿用原始 page_path=%r: %s",
                page_path,
                _rx2,
            )

    # ★ v5.0.3 修复(2026-07-27, §10.9): 优先从 nav_graph_nodes.page_elements 读取（主数据源）
    #   根因(§10.9 3层why):
    #     L1: 所有用例降级为页面可访问性验证
    #     L2: click target_hint 不在 DOM 按钮列表 → §12.2规则2 校验前置降级
    #     L3: _load_dom_elements_for_page 返回空按钮列表
    #     L4: page_dom_snapshots.dom_summary 不含按钮文本（Ant Design button span 嵌套，[N] tag=button 无 text）
    #   修复: nav_graph_nodes.page_elements 是 Step1 爬取时汇总的结构化 JSON，含真实按钮列表，
    #         作为主数据源。命中即 return，避免走 page_dom_snapshots 空数据路径。
    #   通用性(§12.4 9维度):
    #     1. 跨系统: nav_graph_nodes 是 Step1 通用产物，所有系统都有
    #     2. 跨行业: 纯 JSON 字段匹配（buttons/inputs/links），无业务关键词
    #     3. 跨页面: 适用列表/详情/表单/Tab 页
    #     4. 跨Tab: tab 参数精确匹配优先，避免跨 Tab 污染
    #     5. 数据完整性: nav_graph 无数据时降级到 page_dom_snapshots（§12.4 第5条）
    #     6. 根因溯源: 追到 page_dom_snapshots.dom_summary 不含按钮文本
    #     7. 覆盖性: 覆盖所有调用 _load_dom_elements_for_page 的路径
    #     8. 全局观: Step3 拿到真实按钮 → 生成非降级用例 → Step4 真实验证 → Step5 报告准确
    #     9. 职责边界: Step3 内部修复，不越界
    #   自我反驳三次:
    #     1) 漏覆盖? → 命中即 return，未命中走原逻辑，覆盖所有路径
    #     2) 误命中? → tab 参数精确匹配优先，避免跨 Tab 污染
    #     3) 更根本? → 让 Step1 统一写一个数据源改动太大风险高，此为最通用兜底
    try:
        from src.core.config_loader import config as _cfg_nav

        _proj_nav = getattr(_cfg_nav, "project_name", "") or ""
        _ver_nav = getattr(_cfg_nav, "project_version", "") or ""
        if _proj_nav:
            _flat_nav = _load_nav_flat_list(_proj_nav, _ver_nav)
            if _flat_nav:
                import re as _re_nav
                from urllib.parse import unquote as _unq_nav

                _tab_nav = ""
                _m_nav = _re_nav.search(r"[?&]tab=([^&#]+)", page_path)
                if _m_nav:
                    _tab_nav = _unq_nav(_m_nav.group(1))
                _base_nav = page_path.split("?")[0] if "?" in page_path else page_path
                _best_pe_nav = None
                _best_pri_nav = 0
                for _it_nav in _flat_nav:
                    if not isinstance(_it_nav, dict):
                        continue
                    _ip_nav = (
                        _it_nav.get("path", "") or _it_nav.get("page_path", "") or ""
                    )
                    if not _ip_nav:
                        continue
                    _pe_nav = _it_nav.get("page_elements", {})
                    if not _pe_nav or not isinstance(_pe_nav, dict):
                        continue
                    if not (_pe_nav.get("buttons") or _pe_nav.get("inputs")):
                        continue
                    _it_tab_nav = ""
                    _m2_nav = _re_nav.search(r"[?&#]tab=([^&#]+)", _ip_nav)
                    if _m2_nav:
                        _it_tab_nav = _unq_nav(_m2_nav.group(1))
                    _pri_nav = 0
                    if _tab_nav and _it_tab_nav and _tab_nav == _it_tab_nav:
                        _pri_nav = 3
                    elif page_path == _ip_nav:
                        _pri_nav = 2
                    elif _base_nav and _ip_nav.startswith(_base_nav):
                        _pri_nav = 1
                    elif _tab_nav and _it_tab_nav and _tab_nav != _it_tab_nav:
                        continue
                    else:
                        continue
                    if _pri_nav > _best_pri_nav:
                        _best_pri_nav = _pri_nav
                        _best_pe_nav = _pe_nav
                if _best_pe_nav:
                    for _b_nav in _best_pe_nav.get("buttons", []):
                        _bt_nav = (
                            (_b_nav.get("text", "") or "").strip()
                            if isinstance(_b_nav, dict)
                            else str(_b_nav).strip()
                        )
                        if _bt_nav and _bt_nav not in result["button_texts"]:
                            result["button_texts"].append(_bt_nav)
                    for _inp_nav in _best_pe_nav.get("inputs", []):
                        _inp_t_nav = (
                            (
                                _inp_nav.get("placeholder", "")
                                or _inp_nav.get("label", "")
                                or ""
                            ).strip()
                            if isinstance(_inp_nav, dict)
                            else str(_inp_nav).strip()
                        )
                        if (
                            _inp_t_nav
                            and _inp_t_nav not in result["input_placeholders"]
                        ):
                            result["input_placeholders"].append(_inp_t_nav)
                    for _l_nav in _best_pe_nav.get("links", []):
                        _lt_nav = (
                            (_l_nav.get("text", "") or "").strip()
                            if isinstance(_l_nav, dict)
                            else str(_l_nav).strip()
                        )
                        if _lt_nav and _lt_nav not in result["link_texts"]:
                            result["link_texts"].append(_lt_nav)
                    if (
                        result["button_texts"]
                        or result["input_placeholders"]
                        or result["link_texts"]
                    ):
                        _log.info(
                            "[RuleExtract] nav_graph 主数据源命中 (pri=%d, btns=%d, inps=%d, links=%d)",
                            _best_pri_nav,
                            len(result["button_texts"]),
                            len(result["input_placeholders"]),
                            len(result["link_texts"]),
                        )
                        return result
    except Exception as _e_nav:
        _log.error(
            "[RuleExtract] nav_graph 主数据源读取失败 page_path=%r，"
            "本次仅能依赖 rag_chunks 补齐元素: %s",
            page_path,
            _e_nav,
            exc_info=True,
        )

    try:
        from src.core.config_loader import config

        project = getattr(config, "project_name", "") or ""
        if not project:
            return result
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            # 前缀匹配：/dataManage/portraitData?tab=material_formula 匹配
            # /dataManage/portraitData?tab=material_formula&menuKey=formula&yzCode=...
            base_path = page_path.split("?")[0] if "?" in page_path else page_path
            dom_rows = []
            # ★ 通用修复(2026-07-20 v4): 聚合所有同 base_path 的页面按钮
            #   原bug: LIMIT 1 只取一个页面，如果该页面无按钮（如 /dataManage/portraitData 基础页），
            #         就降级，但实际上同 base_path 的 tab 子页面（如 ?tab=material_formula）有按钮。
            #   通用性: 适用于所有"同 base_path 多 tab 页面"的系统（SPA Tab 切换场景）。
            #   覆盖性: 兼容单页面和多 tab 子页面两种场景。
            # ★ P0-1 读侧迁移(2026-08-05): page_dom_snapshots(0 行且无写入方) → nav_graph。
            #   SQL 的 `page_path LIKE 'x%' AND element_count>0 ORDER BY element_count DESC`
            #   等价改为 _nav_dom_rows_by_prefix 的内存前缀过滤 + 排序，语义不变。
            #   原第 3 级回退与第 2 级参数完全相同（均为 base_path+'%'），
            #   属死分支，合并为两级。
            rows = _nav_dom_rows_by_prefix(project, page_path)
            if not rows and base_path != page_path:
                rows = _nav_dom_rows_by_prefix(project, base_path)

            # ★ 通用修复(2026-07-20): rag_chunks查询三级回退
            #   原bug: 用tab参数精确匹配，但用例URL的tab参数（中文URL编码）与rag_chunks中的
            #          tab参数（英文key或香糖料配方URL编码）不匹配，导致查不到PAGE_ELEMENTS。
            #   修复: 三级回退查询
            #     1. tab参数精确匹配（避免跨Tab污染）
            #     2. base_path匹配（兼容URL参数不一致的场景，如某SPA系统的中英文key混用）
            #     3. 无tab参数时用base_path匹配（向后兼容）
            #   通用性: 基于URL参数匹配，不依赖业务逻辑，兼容所有系统的URL形式
            #   合理性: 追根溯源到rag_chunks查询条件过严的缺陷，不同系统URL形式可能不一致
            #   覆盖性: 兼容有tab参数（精确匹配+回退）和无tab参数（base_path兜底）两种场景
            try:
                import re as _re_tab

                _tab_match = _re_tab.search(r"[?&]tab=([^&]+)", page_path)
                _tab_value = _tab_match.group(1) if _tab_match else ""
                if _tab_value:
                    # 第一级：用tab参数精确匹配，避免跨Tab污染
                    _path_pattern = f"%tab={_tab_value}%"
                    dom_rows = (
                        conn.execute(
                            "SELECT text FROM rag_chunks "
                            "WHERE scope_type='project' AND scope_id=%s AND type='dom' AND is_active=1 "
                            "AND text LIKE %s",
                            (project, _path_pattern),
                        ).fetchall()
                        or []
                    )

                    # ★ 第二级：tab参数查不到时，用base_path回退
                    #   原因: 不同系统URL形式可能不一致（如某SPA系统的中英文key混用），
                    #         用例URL的tab参数可能查不到rag_chunks记录。
                    #   修复: 用base_path匹配所有记录，后续通过[PAGE_ELEMENTS]的TABS active标注
                    #         过滤，避免跨Tab污染。
                    if not dom_rows:
                        _log.debug(
                            "[RuleExtract] tab参数 %s 查不到rag_chunks，回退base_path %s",
                            _tab_value[:30],
                            base_path[:60],
                        )
                        dom_rows = (
                            conn.execute(
                                "SELECT text FROM rag_chunks "
                                "WHERE scope_type='project' AND scope_id=%s AND type='dom' AND is_active=1 "
                                "AND text LIKE %s",
                                (project, f"%路径:{base_path}%"),
                            ).fetchall()
                            or []
                        )
                else:
                    # 第三级：无tab参数时用base_path匹配（向后兼容，如非Tab页面）
                    dom_rows = (
                        conn.execute(
                            "SELECT text FROM rag_chunks "
                            "WHERE scope_type='project' AND scope_id=%s AND type='dom' AND is_active=1 "
                            "AND text LIKE %s",
                            (project, f"%路径:{base_path}%"),
                        ).fetchall()
                        or []
                    )
            except Exception as _e:
                _log.warning("[RuleExtract] rag_chunks查询失败: %s", str(_e)[:80])
                dom_rows = []
        finally:
            with guard("_load_dom_elements_for_page: conn.close()", log=_log):
                conn.close()

        import re
        from urllib.parse import unquote as _unquote

        seen_buttons = set()
        seen_inputs = set()
        seen_links = set()

        # 1. 解析 page_dom_snapshots dom_summary（聚合所有匹配行）
        # ★ 通用修复(2026-07-20 v4): 遍历所有 rows（不再只取 rows[0]）
        #   原bug: LIMIT 1 只取一个页面，如果该页面无按钮就降级。
        #   修复: 遍历所有匹配行，聚合按钮（适用于多 Tab 子页面场景）。
        if rows:
            for _row in rows:
                dom_summary = str(_row.get("dom_summary", "") or "")
                if not dom_summary:
                    continue
                # ★ 通用修复(2026-07-20): 优先解析 [PAGE_ELEMENTS] 行
                #   原因: [N] tag=button 没有 text 属性（Ant Design 的 button 用 span 嵌套），
                #         导致按钮列表为空。[PAGE_ELEMENTS] 行包含 Step1 爬取时汇总的真实按钮列表。
                #   通用性: [PAGE_ELEMENTS] 是 Step1 爬取时添加的标准格式，不依赖业务逻辑。
                _pe_result = _parse_page_elements_line(dom_summary)
                for btn in _pe_result.get("button_texts", []):
                    if btn and btn not in seen_buttons:
                        seen_buttons.add(btn)
                        result["button_texts"].append(btn)
                for inp in _pe_result.get("input_placeholders", []):
                    if inp and inp not in seen_inputs:
                        seen_inputs.add(inp)
                        result["input_placeholders"].append(inp)
                for lnk in _pe_result.get("link_texts", []):
                    if lnk and lnk not in seen_links:
                        seen_links.add(lnk)
                        result["link_texts"].append(lnk)
                # ★ v16: 合并 dropdown_triggers(Step1 ARIA 契约爬取)
                for _dd in _pe_result.get("dropdown_triggers", []):
                    _dd_t = _dd.get("trigger", "")
                    if _dd_t and not any(
                        d.get("trigger") == _dd_t for d in result["dropdown_triggers"]
                    ):
                        result["dropdown_triggers"].append(_dd)

                # 回退到 [N] tag=xxx 格式（向后兼容）
                _el_pattern = re.compile(
                    r"\[\d+\]\s+tag=(\w+)\s*(?:\|\s*text=([^|]+?))?\s*(?:\|\s*placeholder=([^|]+?))?\s*(?:\||$)",
                    re.MULTILINE,
                )
                for m in _el_pattern.finditer(dom_summary):
                    tag = m.group(1).lower() if m.group(1) else ""
                    text = (m.group(2) or "").strip()
                    placeholder = (m.group(3) or "").strip()
                    if tag == "button" and text and text not in seen_buttons:
                        seen_buttons.add(text)
                        result["button_texts"].append(text)
                    elif (
                        tag in ("input", "textarea")
                        and placeholder
                        and placeholder not in seen_inputs
                    ):
                        seen_inputs.add(placeholder)
                        result["input_placeholders"].append(placeholder)
                    elif tag == "a" and text and text not in seen_links:
                        seen_links.add(text)
                        result["link_texts"].append(text)

        # 2. ★ Fix48: 解析 rag_chunks dom text（含真实 input/button 元素）
        #   格式: "[页面:xxx] 路径:/path\n元素:[N] tag=input | placeholder=请搜索烟叶名称"
        # ★ 通用修复(2026-07-20): 如果 base_path 回退返回多条记录，需要通过 active Tab 标注过滤，
        #   避免跨 Tab 按钮污染。
        #   策略: 按优先级排序 dom_rows
        #     1. active Tab 名称与用例URL的tab参数（URL解码后）匹配的记录（最高优先级）
        #     2. 有 active Tab 标注的记录
        #     3. 其他记录
        #   通用性: 基于 active Tab 标注匹配，不依赖业务逻辑，兼容所有系统的Tab实现方式。
        _tab_value_decoded = ""
        with guard("_load_dom_elements_for_page: _tab_match2 = re.search(r'[?&]tab=([", log=_log):
            _tab_match2 = re.search(r"[?&]tab=([^&]+)", page_path)
            if _tab_match2:
                _tab_value_decoded = _unquote(_tab_match2.group(1))

        # 按优先级排序 dom_rows
        _priority_rows = []  # active Tab 名称与用例URL的tab参数匹配
        _active_rows = []  # 有 active Tab 标注但名称不匹配
        _other_rows = []  # 无 active Tab 标注
        for dr in dom_rows or []:
            text = str(dr.get("text", "") or "")
            if not text:
                continue
            _pe = _parse_page_elements_line(text)
            _tabs = _pe.get("tabs", [])
            _active_tab = next((t for t in _tabs if t.endswith("(active)")), "")
            _active_tab_name = _active_tab.replace("(active)", "").strip()

            if (
                _active_tab
                and _tab_value_decoded
                and _tab_value_decoded == _active_tab_name
            ):
                _priority_rows.append(dr)
            elif _active_tab:
                _active_rows.append(dr)
            else:
                _other_rows.append(dr)

        _final_dom_rows = _priority_rows + _active_rows + _other_rows

        for dr in _final_dom_rows:
            text = str(dr.get("text", "") or "")
            if not text:
                continue

            # 优先解析 [PAGE_ELEMENTS] 行
            _pe_result = _parse_page_elements_line(text)
            for btn in _pe_result.get("button_texts", []):
                if btn and btn not in seen_buttons:
                    seen_buttons.add(btn)
                    result["button_texts"].append(btn)
            for inp in _pe_result.get("input_placeholders", []):
                if inp and inp not in seen_inputs:
                    seen_inputs.add(inp)
                    result["input_placeholders"].append(inp)
            for lnk in _pe_result.get("link_texts", []):
                if lnk and lnk not in seen_links:
                    seen_links.add(lnk)
                    result["link_texts"].append(lnk)
            # ★ v16: 合并 dropdown_triggers(Step1 ARIA 契约爬取)
            for _dd in _pe_result.get("dropdown_triggers", []):
                _dd_t = _dd.get("trigger", "")
                if _dd_t and not any(
                    d.get("trigger") == _dd_t for d in result["dropdown_triggers"]
                ):
                    result["dropdown_triggers"].append(_dd)

            # 回退到 [N] tag=xxx 格式
            for el_line in text.split("\n"):
                el_line = el_line.strip()
                if not (el_line.startswith("元素:") or el_line.startswith("element:")):
                    continue
                el_content = el_line.split(":", 1)[1].strip() if ":" in el_line else ""
                el_content = re.sub(r"^\[\d+\]\s*", "", el_content)
                dom = _parse_dom_element_line(el_content)
                tag = dom.get("tag", "").lower()
                text_val = dom.get("text", "").strip()
                placeholder = dom.get("placeholder", "").strip()
                if tag == "button" and text_val and text_val not in seen_buttons:
                    seen_buttons.add(text_val)
                    result["button_texts"].append(text_val)
                elif (
                    tag in ("input", "textarea")
                    and placeholder
                    and placeholder not in seen_inputs
                ):
                    seen_inputs.add(placeholder)
                    result["input_placeholders"].append(placeholder)
                elif tag == "a" and text_val and text_val not in seen_links:
                    seen_links.add(text_val)
                    result["link_texts"].append(text_val)

        # 3. ★ P0-3b 修复(2026-07-24): 从 nav_graph_nodes.page_elements 补充（替 system_nav_maps）
        #   根因: 原 v25 修复读 system_nav_maps.nav_json（纸地图），P0-3b 要求切到 nav_graph_nodes。
        #   修复: 调用 _load_nav_flat_list 统一接口，优先 nav_graph_nodes，降级 system_nav_maps。
        #   通用性: nav_graph_nodes 是 Step1 爬取的结构化产物，所有系统都有。
        #   覆盖性: 兼容有 tab 参数（精确匹配）和无 tab 参数（base_path 兜底）两种场景。
        #   合规: AGENTS.md §4.4 转换层禁止格式转换 — _load_nav_flat_list 统一格式输出。
        with guard("_load_dom_elements_for_page: _ver_label = getattr(config, 'projec", log=_log):
            _ver_label = getattr(config, "project_version", "") or ""
            _flat = _load_nav_flat_list(project, _ver_label)
            if _flat:

                # 提取 page_path 的 tab 参数（用于精确匹配，避免跨 Tab 污染）
                _tab_for_match = ""
                _tab_m = re.search(r"[?&]tab=([^&#]+)", page_path)
                if _tab_m:
                    _tab_for_match = _unquote(_tab_m.group(1))

                # 候选页面列表：按匹配优先级排序
                #   优先级1: page_path 完全匹配（含 tab 参数）
                #   优先级2: tab 参数匹配（path 含相同 tab）
                #   优先级3: base_path 匹配（无 tab 参数时）
                _candidates = []
                for _item in _flat:
                    if not isinstance(_item, dict):
                        continue
                    _item_path = (
                        _item.get("path", "") or _item.get("page_path", "") or ""
                    )
                    if not _item_path:
                        continue
                    # 跳过无 page_elements 的条目
                    _pe = _item.get("page_elements", {})
                    if not _pe or not isinstance(_pe, dict):
                        continue
                    _has_buttons = bool(_pe.get("buttons"))
                    _has_inputs = bool(_pe.get("inputs"))
                    if not _has_buttons and not _has_inputs:
                        continue

                    # 匹配判定
                    _item_tab = ""
                    _item_tab_m = re.search(r"[?&#]tab=([^&#]+)", _item_path)
                    if _item_tab_m:
                        _item_tab = _unquote(_item_tab_m.group(1))

                    _priority = 0
                    if _tab_for_match and _item_tab and _tab_for_match == _item_tab:
                        _priority = 3  # tab 参数完全匹配
                    elif page_path == _item_path:
                        _priority = 2  # 完整路径匹配
                    elif base_path and _item_path.startswith(base_path):
                        _priority = 1  # base_path 前缀匹配
                    elif _tab_for_match and _item_tab and _tab_for_match != _item_tab:
                        continue  # tab 参数不同，跳过（避免跨 Tab 污染）
                    else:
                        continue  # 无匹配，跳过

                    _candidates.append((_priority, _item_path, _pe))

                # 按优先级降序排序
                _candidates.sort(key=lambda x: x[0], reverse=True)

                # 从候选页面的 page_elements 提取按钮/输入
                for _prio, _item_path, _pe in _candidates:
                    # 按钮
                    for _btn in _pe.get("buttons", []):
                        _btn_text = ""
                        if isinstance(_btn, dict):
                            _btn_text = (_btn.get("text", "") or "").strip()
                        elif isinstance(_btn, str):
                            _btn_text = _btn.strip()
                        if _btn_text and _btn_text not in seen_buttons:
                            seen_buttons.add(_btn_text)
                            result["button_texts"].append(_btn_text)
                    # 输入框（placeholder 或 label）
                    for _inp in _pe.get("inputs", []):
                        _inp_text = ""
                        if isinstance(_inp, dict):
                            _inp_text = (
                                _inp.get("placeholder", "")
                                or _inp.get("label", "")
                                or ""
                            ).strip()
                        elif isinstance(_inp, str):
                            _inp_text = _inp.strip()
                        if _inp_text and _inp_text not in seen_inputs:
                            seen_inputs.add(_inp_text)
                            result["input_placeholders"].append(_inp_text)
                    # 链接
                    for _lnk in _pe.get("links", []):
                        _lnk_text = ""
                        if isinstance(_lnk, dict):
                            _lnk_text = (_lnk.get("text", "") or "").strip()
                        elif isinstance(_lnk, str):
                            _lnk_text = _lnk.strip()
                        if _lnk_text and _lnk_text not in seen_links:
                            seen_links.add(_lnk_text)
                            result["link_texts"].append(_lnk_text)

                    # ★ v25 根因X3: 只取最高优先级的候选页面（避免跨 Tab 污染）
                    #   如果最高优先级页面有数据，就不再取其他页面
                    if result["button_texts"] or result["input_placeholders"]:
                        _log.debug(
                            "[RuleExtract] _load_dom_elements_for_page: 从 nav_map.page_elements 补充 "
                            "(priority=%d, path=%s, buttons=%d, inputs=%d)",
                            _prio,
                            _item_path[:60],
                            len(result["button_texts"]),
                            len(result["input_placeholders"]),
                        )
                        break

        return result
    except Exception as e:
        _log.debug("[RuleExtract] _load_dom_elements_for_page 失败: %s", str(e)[:80])
        return result


# ── 真实搜索关键词提取（通用，无业务硬编码） ──
# 常见 UI 词汇黑名单（跨系统/跨行业通用）
_SEARCH_UI_BLACKLIST = {
    # 菜单/导航
    "数据看板",
    "数据管理",
    "任务管理",
    "模型管理",
    "数据审编",
    "系统管理",
    "用户管理",
    "首页",
    "主页",
    "登录",
    "注册",
    "退出",
    "设置",
    "个人中心",
    "帮助",
    "关于",
    # 操作按钮
    "新建",
    "新增",
    "添加",
    "创建",
    "编辑",
    "修改",
    "更新",
    "删除",
    "移除",
    "查询",
    "搜索",
    "筛选",
    "检索",
    "查找",
    "导出",
    "下载",
    "导入",
    "上传",
    "确定",
    "确认",
    "取消",
    "关闭",
    "保存",
    "提交",
    "重置",
    "刷新",
    "返回",
    "启用",
    "禁用",
    "停用",
    "批量打标",
    "设置基准",
    "数据导入",
    "批量导出",
    "批量删除",
    "批量操作",
    "全选",
    "反选",
    "展开",
    "折叠",
    "更多",
    "查看",
    # 通用表格列名
    "操作",
    "状态",
    "标签",
    "创建时间",
    "更新时间",
    "序号",
    "编号",
    "名称",
    "产品名称",
    "版型编码",
    "配方单号",
    "牌号名称",
    "牌号编号",
    "类型",
    "备注",
    "描述",
    "数量",
    "金额",
    "价格",
    "日期",
    "时间",
    "负责人",
    "创建人",
    # 通用状态值
    "全部",
    "启用",
    "禁用",
    "待审核",
    "已审核",
    "已发布",
    "草稿",
    "admin",
    "是",
    "否",
    "true",
    "false",
    "null",
    "无",
    "未知",
    # 分页/计数
    "共",
    "条",
    "条数据",
    "页",
    "上一页",
    "下一页",
    "首页",
    "尾页",
}


def _extract_searchable_data_from_dom(page_path: str) -> str:
    """★ 通用修复(2026-07-22): 从 page_dom_snapshots DOM 提取真实搜索关键词。

    根因: search_rule.py 硬编码 "test" 作为搜索值，但目标系统无 "test" 数据，
          导致 text_contains_check 永远失败（假FAIL）。
    通用性: 适用于所有系统的搜索/查询/筛选操作，从 DOM 提取真实显示的数据值。
    合规: AGENTS.md §13.3 禁止硬编码输入值；输入应来自系统真实数据。

    策略:
    1. 从 page_dom_snapshots 加载 DOM summary
    2. 提取 tag=li/td/span 的 text 值（这些是数据记录）
    3. 过滤掉常见 UI 词汇（菜单/按钮/列名/状态值）
    4. 过滤掉太短（≤1字符）、太长（>50字符）、纯数字、日期、版本号
    5. 返回第一个有效的数据值

    返回: 真实数据值（如 "叶组_000"），或空字符串（无数据时）
    """
    if not page_path or page_path == "/login":
        return ""

    # 处理绝对 URL
    if page_path.startswith("http://") or page_path.startswith("https://"):
        try:
            from urllib.parse import urlparse

            page_path = urlparse(page_path).path or "/"
        except ValueError as _rx3:
            # URL 解析失败 → 保留原始 page_path 继续前缀匹配（不影响后续逻辑）
            _log.warning(
                "[RuleExtract] URL 解析失败，沿用原始 page_path=%r: %s",
                page_path,
                _rx3,
            )

    try:
        from src.core.config_loader import config

        project = getattr(config, "project_name", "") or ""
        if not project:
            return ""
        # ★ P0-1 读侧迁移(2026-08-05): page_dom_snapshots → nav_graph（见 _nav_dom_rows_by_prefix）
        base_path = page_path.split("?")[0] if "?" in page_path else page_path
        rows = _nav_dom_rows_by_prefix(project, page_path)
        if not rows and base_path != page_path:
            rows = _nav_dom_rows_by_prefix(project, base_path)

        if not rows:
            _log.debug(
                "[RuleExtract] _extract_searchable_data_from_dom: 无 DOM 数据 page=%s",
                page_path[:60],
            )
            return ""

        import re

        def _filter_data_text(text: str) -> str:
            """通用过滤：返回有效数据值，或空字符串（不合法则跳过）。"""
            text = text.strip()
            # 多行文本取第一行（避免菜单聚合文本如 "叶组配方\n掺配物配方\n..."）
            if "\n" in text:
                text = text.split("\n")[0].strip()
            if not text:
                return ""
            if len(text) <= 1 or len(text) > 50:
                return ""
            if text in _SEARCH_UI_BLACKLIST:
                return ""
            # 纯数字（通常是序号/计数）
            if text.isdigit():
                return ""
            # 包含"条数据"/"共"的计数文本
            if "条数据" in text or text.startswith("共 "):
                return ""
            # 版本号格式（如 V0.2.0）
            if re.match(r"^V\d+\.\d+", text):
                return ""
            # 日期格式（如 2026-06-08）
            if re.match(r"^\d{4}-\d{2}-\d{2}", text):
                return ""
            # 时间戳格式（如 2026-06-08 16:14:06）
            if re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:", text):
                return ""
            # 纯标点符号
            if re.match(r"^[\s\.\,\;\:\!\?\-\_\/\\]+$", text):
                return ""
            return text

        # ★ 两轮提取策略（通用，无业务硬编码）:
        #   第一轮: 只匹配 tag=li/td（数据容器，语义最可靠）
        #   第二轮: 回退到 tag=span（兼容数据只在 span 中的场景）
        #   根因: tag=span 常包含标签文本（如 "叶组"），先匹配到会跳过真实数据（如 "叶组_000"）
        for _tag_pattern in [r"(?:li|td)", r"span"]:
            _data_pattern = re.compile(
                r"\[\d+\]\s+tag=%s\s*\|\s*text=([^\|]+?)(?:\s*\||$)" % _tag_pattern,
                re.MULTILINE,
            )
            for _row in rows:
                dom_summary = str(_row.get("dom_summary", "") or "")
                if not dom_summary:
                    continue
                for m in _data_pattern.finditer(dom_summary):
                    text = _filter_data_text(m.group(1))
                    if text:
                        _log.info(
                            "[RuleExtract] ★通用修复★ _extract_searchable_data_from_dom: "
                            "从 DOM 提取真实搜索关键词: %r (tag=%s, page=%s)",
                            text[:30],
                            _tag_pattern,
                            page_path[:60],
                        )
                        return text

        _log.debug(
            "[RuleExtract] _extract_searchable_data_from_dom: DOM 有数据但无有效搜索值 page=%s",
            page_path[:60],
        )
        return ""
    except Exception as e:
        _log.error(
            "[RuleExtract] _extract_searchable_data_from_dom 失败 page=%r，"
            "搜索关键词将为空: %s",
            page_path,
            e,
            exc_info=True,
        )
        return ""


def _find_page_for_button(button_keywords: list[str]) -> str | None:
    """★ Fix(2026-07-25): navigate-click 页面一致性校验 — 跨页面搜索按钮所在页面。

    当 target_page 的 DOM 按钮不包含 operation 关键词时，搜索导航库中所有页面，
    找到包含匹配按钮的页面，返回其 page_path。

    ★ P0-1 读侧迁移(2026-08-05): page_dom_snapshots → nav_graph（read_dom_pages）。
      根因: 该表 0 行且全仓无写入方，原查询恒返回空 → 跨页面按钮搜索永不命中。

    根因(§10.9 3层why):
      L1: element_exists:✗(找到0个匹配元素) → 导航到错误页面
      L2: 为什么导航到错误页面? → _match_page_path 返回 dashboard
      L3: 为什么返回 dashboard? → nav_text 匹配失败，min() 兜底选最短路径

    通用性(§12.4):
      - 跨系统: 纯基于导航库结构化数据，不依赖任何框架特性
      - 跨行业: 无业务关键词硬编码，仅用 operation 关键词匹配
      - 跨页面: 搜索所有页面（含 Tab 子页面），找到即返回
      - 数据完整性: 无匹配时返回 None，调用方降级处理

    返回: page_path 或 None
    """
    if not button_keywords:
        return None
    try:
        from src.core.config_loader import config

        project = getattr(config, "project_name", "") or ""
        if not project:
            return None
        from src.pipeline.utils.nav_graph_read import read_dom_pages

        rows = [
            p
            for p in read_dom_pages(project)
            if int(p.get("element_count") or 0) > 0
            and str(p.get("page_path", "") or "") != "/login"
        ]
        rows.sort(key=lambda r: int(r.get("element_count") or 0), reverse=True)
        for row in rows:
            ds = str(row.get("dom_summary", "") or "")
            if not ds:
                continue
            # 在 dom_summary 中搜索按钮关键词（任一匹配即可）
            if any(kw in ds for kw in button_keywords):
                pp = str(row.get("page_path", "") or "").strip()
                if pp:
                    _log.info(
                        "[RuleExtract] ★ 跨页面按钮搜索: keywords=%s → 页面 '%s'",
                        button_keywords[:3],
                        pp,
                    )
                    return pp
        # ★ Fix(2026-07-26): 关键词无匹配时，回退到元素最多的非dashboard页面
        #   根因(§10.9 3层why):
        #     L1: 关键词无匹配 → 找不到正确页面
        #     L2: 为什么无匹配? → DOM文本不含需求关键词（如"编辑""辅材"）
        #     L3: 为什么DOM不含关键词? → 系统DOM使用不同词汇（如"配置"代替"编辑"）
        #   修复: 回退到元素最多的非dashboard页面（最可能是功能页面有按钮）
        #   通用性(§12.4): 纯数据驱动，不依赖关键词匹配，跨系统/行业/页面通用
        _non_dashboard = [
            r
            for r in rows
            if "/dashboard" not in str(r.get("page_path", ""))
            and "/login" not in str(r.get("page_path", ""))
        ]
        if _non_dashboard:
            _non_dashboard.sort(
                key=lambda r: len(str(r.get("dom_summary", "") or "")), reverse=True
            )
            pp = str(_non_dashboard[0].get("page_path", "") or "").strip()
            _log.info(
                "[RuleExtract] ★ 跨页面按钮搜索(回退): keywords=%s 无匹配 → 选元素最多页面 '%s'",
                button_keywords[:3],
                pp,
            )
            return pp
    except Exception as e:
        _log.error(
            "[RuleExtract] _find_page_for_button 失败 keywords=%s，跨页面搜索放弃: %s",
            button_keywords[:3],
            e,
            exc_info=True,
        )
    return None
