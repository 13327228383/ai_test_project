# -*- coding: utf-8 -*-
from __future__ import annotations

"""规则提取 - 页面路径匹配模块
================================
★ 架构债清理(2026-07-25): 从 _rule_extractor.py 拆出（§4.3 单文件≤800行）。

职责：从 DOM/API 路径推断目标页面，加载 action_library 操作/断言指令。
通用性（§4.2）：基于 nav_graph 导航数据匹配，无业务关键词硬编码。
"""

import json
import logging
import re
from typing import Any

from src.pipeline.utils.nav_graph_read import read_dom_pages

_log = logging.getLogger(__name__)


def _match_page_path(
    req: dict[str, Any], apis: list[dict], doms: list[dict]
) -> str | None:
    """从匹配的 API 路径推断目标页面路径。

    策略（通用）：
    1. 优先用 DOM 的 page_path（如果 DOM 有匹配的页面）
    2. ★ Fix38 (2026-07-09): RAG 上下文无 DOM page_path 时，直接查导航库（nav_graph）
    3. 否则用 API path 的前缀（如 /dataManagement/auxiliaryFormulation/getList → /dataManagement/auxiliaryFormulation）
    4. 返回最短的路径前缀（更可能是页面根路径）

    ★ Fix35 (2026-07-09): 当 DOM 只有 /login 时，API 路径前缀推断不可靠
    （SPA 的 API 端点前缀 ≠ 页面路由），此时返回 None 让调用方走 AI fallback。
    ★ Fix38 (2026-07-09): RAG 上下文通常不含 DOM page_path（rag_data["doms"]=0），
    导致 Fix35b 无法生效。修复：直接从导航库查询实际页面路径。
    """
    candidates = set()
    dom_page_paths = set()  # ★ Fix35b: 单独记录 DOM 页面路径

    # 1. 从 DOM 提取页面路径
    has_app_pages = False
    for dom in doms:
        pp = dom.get("page_path", "").strip()
        if pp and pp != "/login":  # 排除登录页（业务用例通常不在登录页）
            candidates.add(pp)
            dom_page_paths.add(pp)
            has_app_pages = True

    # ★ Fix38: RAG 上下文无 DOM page_path 时，直接查导航库（nav_graph）
    #   同时用需求的 module/description 匹配 page_title（中文页面名），
    #   选出最相关的页面路径而非简单的 min()。
    _db_pages = []  # ★ Fix38b: 保存完整页面信息供后续匹配
    if not has_app_pages:
        _db_pages = _load_page_paths_from_db()
        if _db_pages:
            for p in _db_pages:
                pp = p.get("page_path", "").strip()
                if pp and pp != "/login":
                    candidates.add(pp)
                    dom_page_paths.add(pp)
                    has_app_pages = True
            _log.debug(
                "[RuleExtract] Fix38: 从 nav_graph 加载 %d 个页面路径", len(_db_pages)
            )

    # ★ Fix35: 只有 DOM 有应用页面时，才用 API 路径前缀推断。
    #   SPA 的 API 端点前缀（如 /dataManagement/auxiliary）与页面路由
    #   （如 /dataManage/portraitData）完全无关，直接使用会导航到404。
    if not has_app_pages and doms:
        # DOM 已爬取但只有 /login → API 路径前缀不可靠
        _log.debug("[RuleExtract] DOM 仅含 /login，跳过 API 路径推断")
        return None

    # 2. 从匹配的 API 路径提取前缀（过滤占位符段 + 动作词段）
    # ★ Fix27 : 修复取3段+无前导/的 bug
    #   原 bug: clean_parts[:3] 取了3段（含 getList 动作词），且缺少前导 /
    #   导致 navigate target_hint='dataManagement/auxiliary/getList'，Step4 跳到错误页面
    #   修复: 取前2段（模块/资源），与 _infer_page_from_api_path 逻辑一致
    for api in apis:
        path = api.get("path", "")
        if not path or not path.startswith("/"):
            continue
        parts = path.split("/")
        # 过滤掉含 {占位符} 的路径段
        clean_parts = [
            p for p in parts[1:] if p and not (p.startswith("{") and p.endswith("}"))
        ]
        # /dataManagement/auxiliary/getList → /dataManagement/auxiliary
        if len(clean_parts) >= 2:
            candidates.add("/" + "/".join(clean_parts[:2]))
        # 单段 API 路径（如 /fmsGetFormulaById）是 API 端点，不是页面路由，跳过

    if not candidates:
        return None

    # ★ Fix35b (2026-07-09): 优先使用 DOM 页面路径，而非 API 路径前缀。
    #   SPA 的 API 端点前缀（如 /dataManagement/auxiliary）与页面路由
    #   （如 /dataManage/portraitData）完全无关。
    #   DOM page_paths 来自实际页面爬取，比 API 路径前缀更可靠。
    #   当两者都存在时，优先 DOM page_paths。
    dom_candidates = candidates & dom_page_paths  # 仅 DOM 页面路径
    if dom_candidates:
        # ★ Fix38b: 有多个 DOM 页面路径时，用需求语义匹配最佳页面
        if len(dom_candidates) > 1 and _db_pages:
            best_path = _match_best_page_for_req(req, _db_pages)
            if best_path and best_path in dom_candidates:
                return best_path
        # 兜底：返回第一个 DOM 页面路径
        return list(dom_candidates)[0]

    # ★ Fix38: 有 DOM 页面路径但 API 前缀不在其中 → 用需求语义匹配最佳页面
    if dom_page_paths:
        # ★ Fix38b: 用需求的 module/description 匹配 page_title（中文页面名）
        best_path = _match_best_page_for_req(req, _db_pages)
        if best_path:
            return best_path
        # ★ Fix(2026-07-26): 需求语义匹配失败时，优先选元素最多的页面（非dashboard）
        #   根因(§10.9 3层why):
        #     L1: element_exists:✗ → 导航到 dashboard
        #     L2: 为什么导航到 dashboard? → min(dom_page_paths, key=len) 选了最短路径"/dashboard"
        #     L3: 为什么 min(len) 是 dashboard? → dashboard 路径最短，但功能最少
        #   全局观: 影响所有 case_rules 的 navigate 目标，导致 Step4 导航到错误页面
        #   修复: 优先选元素最多的页面（非dashboard/login），最可能是功能页面有按钮
        #   通用性(§12.4):
        #     - 跨系统: 纯基于 dom_summary 长度（元素数代理），不依赖任何框架特性
        #     - 跨行业: 无业务关键词硬编码，页面对比纯数据驱动
        #     - 跨页面: 排除 dashboard/login 后选最丰富的页面
        #     - 数据完整性: 无 _db_pages 时回退到原 min(len) 逻辑
        if _db_pages:
            _non_dashboard = [
                p
                for p in _db_pages
                if p.get("page_path", "") in dom_page_paths
                and "/dashboard" not in p.get("page_path", "")
                and "/login" not in p.get("page_path", "")
            ]
            if _non_dashboard:
                _non_dashboard.sort(
                    key=lambda p: len(p.get("dom_summary", "") or ""), reverse=True
                )
                _best = _non_dashboard[0].get("page_path", "")
                _log.info(
                    "[RuleExtract] ★ 页面回退策略: 语义匹配失败，选元素最多页面 '%s' (dom_len=%d)",
                    _best,
                    len(_non_dashboard[0].get("dom_summary", "") or ""),
                )
                return _best
        return min(dom_page_paths, key=len)

    # 无 DOM 页面路径 → 使用 API 路径前缀（fallback）
    return min(candidates, key=len)


def _load_page_paths_from_db() -> list[dict[str, str]]:
    """加载当前项目的页面路径 + DOM 摘要 + 导航文本（来源：nav_graph_nodes）。

    ★ P0-1 读侧收口(2026-08-05): page_dom_snapshots → nav_graph（read_dom_pages）。
      根因(§10.9 3层why):
        L1: 页面匹配总是拿不到候选 → _match_best_page_for_req 无输入 → target_page 退化为 "/"。
        L2: 为什么无候选? → 主查询读 page_dom_snapshots，该表 0 行且**全仓无写入方**。
        L3: 为什么没人发现? → 外层 `except: _log.debug` 把失败压成 debug，
            与"项目确实没爬过"完全不可区分。
      修复: 单一数据源 nav_graph_nodes —— page_path/page_title 来自真实爬取，
      dom_summary 由结构化 page_elements 合成，nav_text 直接取 page_title
      （原先要额外查一次 nav_graph 才能补的字段，现在同源一次拿全）。

    nav_text 的作用（保持不变）: Tab 页面的 dom_summary 常含共享菜单文字，
    仅靠 dom_summary 关键词匹配会在所有 Tab 上同分；nav_text 是页面自身标题，
    能让 operation="查询XX配方" 精确命中对应 Tab。

    通用性(§12.4): 纯结构化字段映射，无业务关键词/路径硬编码，跨系统通用。
    """
    from src.core.config_loader import config

    project = getattr(config, "project_name", "") or ""
    if not project:
        _log.warning("[RuleExtract] project 未配置，无法加载页面路径候选集")
        return []

    try:
        pages = read_dom_pages(project)
    except Exception as e:
        _log.error(
            "[RuleExtract] _load_page_paths_from_db 加载失败 project=%s，"
            "页面匹配将无候选集: %s",
            project,
            e,
            exc_info=True,
        )
        return []

    # 按 page_path 去重聚合：同一路径的多条快照合并 dom_summary（等价旧 GROUP_CONCAT）
    merged: dict[str, dict[str, str]] = {}
    for page in pages:
        page_path = str(page.get("page_path", "") or "").strip()
        if not page_path:
            continue
        title = str(page.get("page_title", "") or "").strip()
        summary = str(page.get("dom_summary", "") or "").strip()
        existing = merged.get(page_path)
        if existing is None:
            merged[page_path] = {
                "page_path": page_path,
                "page_title": title,
                "dom_summary": summary,
                # nav_text 即导航节点标题，用于 operation→Tab 的精确匹配
                "nav_text": title,
            }
            continue
        if summary and summary not in existing["dom_summary"]:
            existing["dom_summary"] = (existing["dom_summary"] + " " + summary).strip()
        if title and not existing["page_title"]:
            existing["page_title"] = title
            existing["nav_text"] = title

    result = list(merged.values())
    _log.info(
        "[RuleExtract] _load_page_paths_from_db: nav_graph 页面候选 %d 条 (project=%s)",
        len(result),
        project,
    )
    return result


def _match_best_page_for_req(
    req: dict[str, Any], db_pages: list[dict[str, str]]
) -> str | None:
    """★ Fix38b (2026-07-09): 用需求的 module/description/operation 匹配页面内容选择最佳页面。
    ★ 通用修复(2026-07-20): 优先用 nav_text（Tab名称）匹配，避免共享菜单文字干扰。

    策略（通用，无业务硬编码）：
    0. ★ 优先用需求 operation 精确匹配 nav_text（Tab 名称）。
       修复前: 所有 Tab 页面 dom_summary 都含共享菜单文字 → "辅材"在所有Tab都匹配
       → 随机选到第一个Tab（叶组配方）。
       修复后: nav_text="辅材配方" 与 operation="查询辅材配方" 精确子串匹配 → 正确Tab。
    1. 提取需求的 module + description + operation 中的中文词组（2-4字）
    2. 用中文词组匹配 dom_summary 的 [PAGE_ELEMENTS] 段（页面特有交互元素）
    3. 也匹配 page_path 的驼峰片段
    4. 按匹配分数排序，返回最佳匹配
    5. 无匹配时返回 None（由调用方用 min() 兜底）

    通用性：不硬编码任何行业关键词，纯基于中文词组匹配 + 驼峰路径匹配。
    """
    if not db_pages:
        return None

    module = str(req.get("module", "") or "").strip()
    desc = str(req.get("description", "") or "").strip()
    operation = str(req.get("operation", "") or "").strip()
    combined = f"{module} {desc} {operation}"

    # ★ 通用修复(2026-07-20): 优先用 operation 精确匹配 nav_text
    #   这是 SPA Tab 页面最可靠的匹配方式：nav_text 是 DOM 爬虫从菜单中提取的
    #   Tab 名称（如"辅材配方"），operation 是需求中的操作描述（如"查询辅材配方"）。
    #   子串匹配双向：nav_text∈operation 或 operation∈nav_text
    if operation:
        _op_lower = operation.lower()
        _exact_matches = []
        for p in db_pages:
            _nt = p.get("nav_text", "") or ""
            if not _nt:
                continue
            _nt_lower = _nt.lower()
            # 双向子串匹配（去空格）
            _op_compact = _op_lower.replace(" ", "").replace("　", "")
            _nt_compact = _nt_lower.replace(" ", "").replace("　", "")
            if (
                _nt_compact
                and _op_compact
                and (_nt_compact in _op_compact or _op_compact in _nt_compact)
            ):
                _exact_matches.append((p, len(_nt)))  # 优先较长的 nav_text（更具体）
        if _exact_matches:
            _exact_matches.sort(key=lambda x: -x[1])  # 按 nav_text 长度降序
            best_p = _exact_matches[0][0]
            _log.info(
                "[RuleExtract] ★ nav_text匹配: operation='%s' → nav_text='%s' → path=%s",
                operation,
                best_p.get("nav_text", ""),
                best_p.get("page_path", ""),
            )
            return best_p.get("page_path", "")

    # 提取中文词组（2-4字）
    cn_chars = re.findall(r"[\u4e00-\u9fff]", combined)
    if not cn_chars:
        return None

    cn_words = set()
    for length in (2, 3, 4):
        for i in range(len(cn_chars) - length + 1):
            cn_words.add("".join(cn_chars[i : i + length]))

    if not cn_words:
        return None

    best_path = None
    best_score = 0
    for p in db_pages:
        dom_summary = p.get("dom_summary", "") or ""
        page_path = p.get("page_path", "") or ""
        nav_text = p.get("nav_text", "") or ""
        score = 0

        # ★ 通用修复(2026-07-20): nav_text 匹配权重最高（页面特有名称）
        if nav_text:
            for word in cn_words:
                if word in nav_text:
                    score += 5  # nav_text 是页面特有名称，权重最高

        # ★ 通用修复(2026-07-20): 只用 [PAGE_ELEMENTS] 段做 dom_summary 匹配
        #   这段是页面特有的交互元素（inputs/buttons/tabs），不含共享菜单文字。
        #   避免"辅材"出现在所有Tab的共享菜单中导致误匹配。
        pe_section = ""
        if "[PAGE_ELEMENTS]" in dom_summary:
            pe_section = dom_summary.split("[PAGE_ELEMENTS]", 1)[1]
        else:
            pe_section = dom_summary  # 无 [PAGE_ELEMENTS] 时用全文本（兜底）
        for word in cn_words:
            if word in pe_section:
                score += 2  # page_elements 匹配权重中

        # 3. 用中文词组匹配 page_path 的驼峰片段（通用，弱关联）
        segments = [s for s in page_path.split("/") if s]
        for seg in segments:
            seg_words = re.sub(r"([A-Z])", r" \1", seg).strip().lower().split()
            seg_words.append(seg.lower())
            for sw in seg_words:
                if len(sw) >= 3:
                    for word in cn_words:
                        if word.lower() in sw:
                            score += 1

        if score > best_score:
            best_score = score
            best_path = page_path

    if best_path and best_score > 0:
        _log.info(
            "[RuleExtract] Fix38b: 页面匹配 '%s' → %s (score=%d)",
            module or desc or operation,
            best_path,
            best_score,
        )
        return best_path
    return None


def _match_dom_element(
    req: dict[str, Any], doms: list[dict], action_type: str
) -> dict | None:
    """根据需求操作类型匹配 DOM 元素。

    策略（通用，模拟人类找元素）：
    - navigate → 不需要元素
    - click → 找 button 或 a 标签（或 role=button）
    - fill → 找 input 或 textarea（有 placeholder 的优先）
    - select_option → 找 select 标签
    - verify → 找有 text 的元素
    """
    operation = str(req.get("operation", "") or "").lower()

    if action_type == "click":
        # 找 button/a 标签
        candidates = [
            d
            for d in doms
            if d.get("tag") in ("button", "a") or d.get("role") == "button"
        ]
        # 如果操作包含"登录"、"提交"、"保存"等通用动作词，优先匹配 text 包含该词的按钮
        _action_words = [
            "登录",
            "提交",
            "保存",
            "确认",
            "取消",
            "删除",
            "编辑",
            "新建",
            "查询",
            "搜索",
            "导出",
            "导入",
        ]
        for word in _action_words:
            if word in operation:
                for d in candidates:
                    if word in d.get("text", ""):
                        return d
        # 没有匹配词，返回第一个 button
        if candidates:
            return candidates[0]

    elif action_type == "fill":
        # 找 input/textarea
        candidates = [d for d in doms if d.get("tag") in ("input", "textarea")]
        if candidates:
            return candidates[0]

    elif action_type == "select_option":
        candidates = [d for d in doms if d.get("tag") == "select"]
        if candidates:
            return candidates[0]

    return None


# ============================================================
# 3. action_library 查询（操作指令和断言指令）
# ============================================================


def _load_action_library() -> tuple[list[dict], list[dict]]:
    """从 action_actions 表加载操作指令和断言指令。

    返回:
        (operations, assertions)
        operations: [{"action_id", "label", "applicable_tags", "tool_name"}, ...]
        assertions: [{"action_id", "label", "tool_name"}, ...]
    """
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT action_id, label, applicable_tags, tool_name, category_id "
                "FROM action_actions ORDER BY action_id"
            ).fetchall()
        finally:
            conn.close()

        operations = []
        assertions = []
        for r in rows or []:
            tags_str = r.get("applicable_tags", "[]") or "[]"
            try:
                tags = json.loads(tags_str) if isinstance(tags_str, str) else tags_str
            except Exception:
                tags = []

            item = {
                "action_id": r["action_id"],
                "label": r.get("label", ""),
                "applicable_tags": tags,
                "tool_name": r.get("tool_name", ""),
            }
            cat = (r.get("category_id") or "").lower()
            if cat == "verification":
                assertions.append(item)
            else:
                operations.append(item)

        _log.debug(
            "[RuleExtract] action_library: %d operations, %d assertions",
            len(operations),
            len(assertions),
        )
        return operations, assertions
    except Exception as e:
        _log.warning("[RuleExtract] 加载 action_library 失败: %s", str(e)[:80])
        return [], []


def _select_operations_for_control(
    control_type: str, operations: list[dict]
) -> list[dict]:
    """根据控件类型选择适用的操作指令。

    策略（通用，基于 applicable_tags 匹配，不硬编码业务）：
    1. 通用兜底操作（navigate/click/screenshot/verify）始终保留
    2. 其他操作要求 applicable_tags 包含控件类型对应的 HTML tag
    """
    _control_tags_map = {
        "input": ["input", "textarea"],
        "textarea": ["textarea"],
        "select": ["select"],
        "button": ["button", "a"],
        "upload": ["input"],
    }
    control_tags = set(_control_tags_map.get(control_type, []))

    _always_keep = {"navigate", "click", "screenshot", "verify"}
    selected = []
    for op in operations:
        aid = op.get("action_id", "")
        if aid in _always_keep:
            selected.append(op)
            continue
        op_tags = set(op.get("applicable_tags", []) or [])
        if control_tags and op_tags & control_tags:
            selected.append(op)

    return selected


def _select_assertions_for_tracks(
    required_tracks: list[str], assertions: list[dict]
) -> list[dict]:
    """根据校验轨道选择适用的断言指令。

    策略（通用，基于断言 action_id 的语义）：
    - track="ui" → 选 verify_style/verify_toast/element_exists/element_text 等前端断言
    - track="api" → 选 api_verify/api_response_body_check/api_status_code_check 等接口断言
    - track="db" → 选 db_verify/db_row_count/db_field_value 等数据库断言
    """
    _track_assertion_map = {
        "ui": [
            "verify_style",
            "verify_toast",
            "verify_position",
            "element_exists",
            "element_text",
            "no_error",
            "url_contains",
            "animation_complete_check",
        ],
        "api": [
            "api_verify",
            "api_response_body_check",
            "api_status_code_check",
            "api_call_count_check",
            "api_header_check",
            "response_time_check",
        ],
        "db": ["db_verify", "db_row_count", "db_field_value"],
    }

    selected = []
    seen_ids = set()
    for track in required_tracks:
        target_ids = _track_assertion_map.get(track, [])
        for aid in target_ids:
            if aid in seen_ids:
                continue
            for a in assertions:
                if a.get("action_id") == aid:
                    selected.append(a)
                    seen_ids.add(aid)
                    break

    # 兜底：如果没有匹配到断言，至少返回 element_exists 和 no_error
    if not selected:
        for a in assertions:
            if a.get("action_id") in ("element_exists", "no_error"):
                selected.append(a)
                if len(selected) >= 2:
                    break

    return selected
