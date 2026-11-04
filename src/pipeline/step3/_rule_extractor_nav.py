# -*- coding: utf-8 -*-
"""_rule_extractor_nav — 从 _rule_extractor.py 拆分（D.1g 架构债清理 2026-07-25，§4.3 单文件≤800行）。

职责：nav 相关函数。
逻辑不变，仅结构重构。re-export 保持调用兼容。
"""
from __future__ import (
    annotations,
)  # ★ 修复(2026-07-25): Python 3.9 兼容（PROJECT_MEMORY.md 硬约束）
from src.core.guarded import guard
import json
import logging
import re
from collections import Counter
from typing import Any

_log = logging.getLogger(__name__)

from ._rule_extractor_pages import (  # noqa: F401
    _load_action_library,
    _load_page_paths_from_db,
    _match_best_page_for_req,
    _match_dom_element,
    _match_page_path,
    _select_assertions_for_tracks,
    _select_operations_for_control,
)


def _edge_nav_context(edge: dict) -> tuple[str, bool]:
    """取一条 nav_graph 边的「可用导航上下文文本」。

    ★ R1 收尾(2026-08-05) [已复核-DeepSeek-V4-Flash 2026-08-05]：
      背景：`nav_graph_store._resolve_edge_rows` 已把 link_text 兜底写进
      trigger_action.selector（真实库 270 条链接派生边），但这些边的
      via_sidebar 为空（扁平超链接确实没有菜单上下文，不回填是诚实表达）。
      原实现只认 via_sidebar，导致这 270 条边在下游全被跳过。
    取值优先级：via_sidebar（真菜单上下文）> trigger_action.selector（链接文案）。
    fail-closed：两者皆空 → 返回 ("", False)，调用方必须跳过该边。

    返回:
        (context_text, is_sidebar) —— is_sidebar 用于同分时优先真菜单边。
    """
    sidebar = (edge.get("via_sidebar", "") or "").strip()
    if sidebar:
        return sidebar, True

    trigger = edge.get("trigger_action") or ""
    if isinstance(trigger, (bytes, bytearray)):
        trigger = trigger.decode("utf-8", errors="ignore")
    if isinstance(trigger, str):
        if not trigger.strip():
            return "", False
        try:
            trigger = json.loads(trigger)
        except (ValueError, TypeError):
            _log.debug(
                "[RuleExtract] trigger_action 非法 JSON，跳过该边: %s", trigger[:60]
            )
            return "", False
    if not isinstance(trigger, dict):
        return "", False

    selector = str(trigger.get("selector", "") or "").strip()
    return (selector, False) if selector else ("", False)


def _find_nav_path_for_req(req: dict) -> list[str] | None:
    """★ 治本修复 (2026-07-17): 从 system_nav_maps 查找需求的菜单导航路径。

    当 page_dom_snapshots 无法匹配到具体页面路径时（如 SPA 应用），
    用 nav_map 的 menu_groups 查找匹配的菜单项，返回完整导航路径。

    策略（通用，无业务硬编码）：
    1. 提取需求的 module + operation 中的中文关键词
    2. 在 nav_map menu_groups 中匹配 item.text
    3. 返回 parent_path + item.text 的完整路径
    4. 无匹配时返回 None

    返回: ["数据管理", "物料管理", "辅材管理"] 或 None
    """
    import re as _re_nav

    module = str(req.get("module", "") or "").strip()
    operation = str(req.get("operation", "") or "").strip()
    combined = f"{module} {operation}"

    # 提取中文关键词（2-4字），并拆分4字词为2字子串以提高匹配率
    cn_words_raw = _re_nav.findall(r"[\u4e00-\u9fff]{2,4}", combined)
    seen = set()
    keywords = []
    for w in cn_words_raw:
        if w not in seen:
            seen.add(w)
            keywords.append(w)
        # 4字词拆分：如"新建模型" → 也加入"新建"、"模型"
        if len(w) == 4:
            for i in range(0, 4, 2):
                sub = w[i : i + 2]
                if sub not in seen:
                    seen.add(sub)
                    keywords.append(sub)
        # 3字词拆分：如"查询辅材" → 也加入"查询"、"辅材"
        elif len(w) == 3:
            for i in range(0, 3, 2):
                sub = w[i : i + 2]
                if sub not in seen:
                    seen.add(sub)
                    keywords.append(sub)

    with guard("_find_nav_path_for_req: from src.core.config_loader import c", log=_log):
        from src.core.config_loader import config

        project = getattr(config, "project_name", "") or ""
        if not project:
            return None
        # ★ P0-3b 读侧迁移(2026-07-26): nav_graph_edges.via_sidebar 替 system_nav_maps.menu_groups
        #   根因(§10.9): system_nav_maps 已停写（0行），nav_graph_edges 有真实 sidebar 数据
        from src.core.versioning import current_version_label

        _vl = current_version_label(project) if project else ""
        if _vl:
            from src.pipeline.utils.nav_graph_store import read_nav_graph_edges

            _edges = read_nav_graph_edges(project, _vl)
            best_score = 0
            best_sidebar = ""
            best_is_sidebar = False
            for e in _edges:
                # ★ R1 收尾(2026-08-05) [已复核-DeepSeek-V4-Flash 2026-08-05]：
                #   守卫由 `if not via_sidebar: continue` 放宽为「无任何导航上下文才跳过」，
                #   否则链接派生边（via_sidebar 空、selector=link_text）永远匹配不到，
                #   扁平导航系统的 _find_nav_path_for_req 恒返回 None。
                _ctx, _is_sb = _edge_nav_context(e)
                if not _ctx:
                    continue  # fail-closed：sidebar 与 selector 皆空，无据可依
                score = 0
                for kw in keywords:
                    if kw in _ctx:
                        score += 10
                if score <= 0:
                    continue
                # 同分时真菜单边（via_sidebar）优先于扁平链接边——菜单上下文证据更强
                if score > best_score or (
                    score == best_score and _is_sb and not best_is_sidebar
                ):
                    best_score = score
                    best_sidebar = _ctx
                    best_is_sidebar = _is_sb
            if best_sidebar and best_score > 0:
                _log.info(
                    "[RuleExtract] nav_graph 匹配: '%s' → %s '%s' (score=%d)",
                    keywords[:3],
                    "sidebar" if best_is_sidebar else "link",
                    best_sidebar,
                    best_score,
                )
                return [best_sidebar]

    return None


def _find_sidebar_for_target(target_page: str, req: dict) -> tuple[str, str] | None:
    """★ 通用修复(2026-07-20 v3): 从 nav_map 查找 req 匹配的虚拟URL sidebar_text。

    当 SPA 侧边栏菜单点击后 URL 不变时，SpaMenuExplorer 合成虚拟URL
    (real_url#sidebar=text)。本函数查询 nav_map 找到匹配的 sidebar_text，
    供 Step3 生成 navigate(real_url) + click(sidebar_text) 复合 action 序列。

    ★ v3 修复根因: v2 要求 target_page == real_url，但 target_page 常是兜底值
      "/" 或 "/dashboard"（策略1/2 失败时），导致永远不匹配。
      v3 改为不依赖 target_page，直接用 req.operation/module 匹配 sidebar_text。
    通用性: 适用于所有"侧边栏菜单点击后URL不变"的SPA系统。
    合理性: 追根溯源到 req 语义 → nav_map sidebar_text 的直接匹配，不依赖 target_page。
    覆盖性: 兼容 target_page 为兜底值和正确值两种场景。

    策略（通用，无业务硬编码）：
    1. 从 nav_map flat_list 筛选含 #sidebar= 的项
    2. 用需求 operation/module 提取中文关键词
    3. 匹配 sidebar_text，返回最佳匹配的 (real_url, sidebar_text)
    """
    operation = str(req.get("operation", "") or "").strip()
    module = str(req.get("module", "") or "").strip()
    description = str(req.get("description", "") or "").strip()
    combined = f"{module} {operation} {description}"
    import re as _re_sb

    # ★ 通用修复(2026-07-20 v5): 提取所有 2-4 字中文子串（滑动窗口），避免贪婪匹配漏词
    #   原bug: re.findall(r'[\u4e00-\u9fff]{2,4}', "查询辅材") 只返回 ["查询辅材"]，
    #         "查询辅材" in "辅材管理" 为 False，导致 REQ-001 等用例无法匹配 sidebar='辅材管理'。
    #   修复: 用滑动窗口提取所有 2/3/4 字子串，如"查询辅材" → ["查询", "询辅", "辅材", "查询辅", "询辅材", "查询辅材"]。
    #   通用性: 适用于所有中文操作词与 sidebar_text 的部分匹配场景，不依赖业务逻辑。
    #   合理性: 追根溯源到正则贪婪匹配的缺陷，滑动窗口是通用解法。
    #   覆盖性: 兼容全词匹配和部分匹配两种场景。
    def _extract_cn_substrings(text: str) -> list:
        """提取所有 2-4 字中文子串（滑动窗口）。"""
        if not text:
            return []
        # 先找连续中文字符段
        _segments = _re_sb.findall(r"[\u4e00-\u9fff]+", text)
        _result = []
        for _seg in _segments:
            _seg_len = len(_seg)
            for _size in (2, 3, 4):
                if _seg_len < _size:
                    continue
                for _i in range(_seg_len - _size + 1):
                    _sub = _seg[_i : _i + _size]
                    if _sub not in _result:
                        _result.append(_sub)
        return _result

    cn_words = _extract_cn_substrings(combined)
    if not cn_words:
        return None

    with guard("_find_sidebar_for_target: from src.core.config_loader import c", log=_log):
        from src.core.config_loader import config

        project = getattr(config, "project_name", "") or ""
        if not project:
            return None
        # ★ P0-3b 读侧迁移(2026-07-26): nav_graph_nodes 替 system_nav_maps.nav_json flat_list
        #   根因(§10.9): system_nav_maps 已停写（0行），nav_graph_nodes 有真实 sidebar 数据
        #   通用性: nav_graph_nodes.page_path 含 #sidebar= 的条目即为 sidebar 页面
        from src.core.versioning import current_version_label

        _vl = current_version_label(project) if project else ""
        _sidebar_items = []  # [(real_url, sidebar_text, page_elements, path_len)]
        if _vl:
            from src.pipeline.utils.nav_graph_store import read_nav_graph_nodes

            _nodes = read_nav_graph_nodes(project, _vl)
            for n in _nodes:
                _pp = n.get("page_path", "") or n.get("page_url", "") or ""
                if "#sidebar=" not in _pp:
                    continue
                # 解析 sidebar_text（从 #sidebar=xxx 提取）
                _sb_match = _re_sb.search(r"#sidebar=(.+)", _pp)
                _sb_text = _sb_match.group(1) if _sb_match else ""
                if not _sb_text:
                    continue
                _real_url = n.get("page_url", "") or _pp.split("#sidebar=")[0]
                _pe = n.get("page_elements", {})
                if isinstance(_pe, str):
                    try:
                        import json as _json_pe

                        _pe = _json_pe.loads(_pe)
                    except Exception:
                        _pe = {}
                _sidebar_items.append((_real_url, _sb_text, _pe, len(_pp)))

        # ★ v3: 不再要求 target_page == real_url，直接用 req 关键词匹配 sidebar_text
        # 原因: target_page 常是兜底值 "/" 或 "/dashboard"，与 real_url 永远不匹配
        # ★ v25 根因Y2 修复: 修复"管理"等通用后缀导致错误匹配
        #   原bug: module="辅材配方管理"的"管理"两字匹配到 sidebar_text="数据管理"(score=10)，
        #         但"数据管理"有两条记录(/dashboard 和 /dataManage/portraitData)，选第一个(/dashboard)是错的。
        #   根因: 通用后缀词（管理/系统/数据/操作）匹配 score 与业务词（辅材/配方）相同，
        #         导致通用后缀词的 score 压过业务词，匹配到错误的 sidebar。
        #   修复:
        #     1. 通用后缀词（管理/系统/数据/操作/信息/配置/查询/列表/详情）匹配 score 降权（1分）
        #     2. 业务词（非通用后缀）匹配 score 保持 10 分
        #     3. score 相同时，优先选 page_elements 有按钮的 real_url（有按钮=真实业务页面）
        #     4. score 和 btns 都相同时，优先选 path 更长的 real_url（更具体）
        #   通用性: 通用后缀词列表跨系统通用（所有系统的"管理/系统/数据"都是导航词不是业务词）。
        #   覆盖性: 影响 _find_sidebar_for_target 的所有匹配。
        #   合规: AGENTS.md §10.8 禁止治标不治本 — 修匹配算法权重，不靠 DOM 校验兜底。
        _GENERIC_SUFFIX_WORDS = frozenset(
            {
                "管理",
                "系统",
                "数据",
                "操作",
                "信息",
                "配置",
                "查询",
                "列表",
                "详情",
                "功能",
                "页面",
                "模块",
                "菜单",
                "视图",
                "面板",
                "区域",
                "中心",
                "平台",
            }
        )
        best_score = 0
        best_match = None  # (real_url, sidebar_text)
        best_btns = -1  # ★ v25: 记录最佳匹配的按钮数，用于 tie-breaker
        best_path_len = -1  # ★ v25: 记录最佳匹配的 path 长度，用于 tie-breaker
        for _real_url, _sidebar_text, _pe, _path_len in _sidebar_items:
            # ★ 过滤明显无业务语义的 sidebar_text（如"暂无数据""初始"等占位符）
            _invalid_texts = {
                "暂无数据",
                "初始",
                "封面",
                "左对齐",
                "画框",
                "详情",
                "操作",
            }
            if _sidebar_text in _invalid_texts:
                continue
            # 用 operation 关键词匹配 sidebar_text
            _score = 0
            # ★ v25 根因Y2-3 修复: _sb_words 在 kw 循环外计算，避免重复加分
            #   原bug: _sb_words 在 for kw 循环内计算，每个 kw 都触发完整反向匹配，
            #         导致"管理"被重复加分 17 次（cn_words 数量），score 虚高到 19。
            #   修复: _sb_words 在循环外计算一次，反向匹配只算一次。
            _sb_words = _extract_cn_substrings(_sidebar_text)
            for kw in cn_words:
                if kw in _sidebar_text:
                    # ★ v25: 通用后缀词降权（1分），业务词保持 10 分
                    if kw in _GENERIC_SUFFIX_WORDS:
                        _score += 1
                    else:
                        _score += 10
            # 反向匹配：sidebar_text 中的词也在 req 中（只算一次）
            for sb_kw in _sb_words:
                if sb_kw in combined:
                    # ★ v25: 反向匹配也降权通用后缀词
                    if sb_kw in _GENERIC_SUFFIX_WORDS:
                        _score += 1
                    else:
                        _score += 5
            # ★ v25: tie-breaker - 按钮数 + path 长度
            _item_btns = 0
            if isinstance(_pe, dict):
                _item_btns = len(_pe.get("buttons", []))
            _item_path_len = _path_len

            # ★ v25: 综合排序 - score > btns > path_len
            _is_better = False
            if _score > best_score:
                _is_better = True
            elif _score == best_score and _score > 0:
                if _item_btns > best_btns:
                    _is_better = True
                elif _item_btns == best_btns:
                    if _item_path_len > best_path_len:
                        _is_better = True
            if _is_better:
                best_score = _score
                best_match = (_real_url, _sidebar_text)
                best_btns = _item_btns
                best_path_len = _item_path_len

        if best_match and best_score >= 5:
            # ★ v25 根因Y2-2 修复: 阈值从 1 提升到 5
            #   原bug: 阈值=1 时，"管理"等通用后缀词（1分）就能匹配，导致错误映射。
            #   修复: 阈值=5 要求至少有一个业务词匹配（业务词=10分，通用后缀词=1分），
            #         纯通用后缀词匹配（最高4分）会被过滤，避免错误映射。
            #   通用性: 适用于所有系统（业务词匹配 score >= 5 是通用阈值）。
            #   合规: AGENTS.md §10.8 — 宁可降级也不错误匹配。
            _log.info(
                "[RuleExtract] ★v25 Y2-2★ best_match=%s best_score=%d (阈值=5)",
                str(best_match)[:80],
                best_score,
            )
            # ★ 通用修复(2026-07-20 v7): DOM 校验前置 — sidebar_text 必须在真实 DOM 中存在
            #   原bug: nav_map 中的 sidebar 条目可能来自 Confluence 文档（source=confluence），
            #         是规划中的功能名而非实际菜单项。如 某SPA系统 的"辅材管理"来自 Confluence，
            #         但 某SPA系统 实际菜单是"数据看板/数据管理/任务管理/模型管理/数据审编"，
            #         "辅材管理"根本不存在于任何页面的 DOM 中。
            #   根因: _find_sidebar_for_target 只查 nav_map，不校验 sidebar_text 是否在 DOM 中真实存在，
            #         导致虚构 sidebar 被注入 actions，Step4 执行时 click("辅材管理") 永远找不到元素。
            #   修复: 返回 best_match 前，调用 _verify_sidebar_in_dom 校验 sidebar_text 是否在
            #         page_dom_snapshots 的 button_texts/link_texts/menuitem 文本中真实存在。
            #         校验失败 → 视为虚构条目，返回 None（触发降级用例）。
            #   通用性: 基于真实 DOM 数据校验，不依赖 nav_map 来源标记（confluence/dom/figma），
            #          适用于所有系统/行业/页面。
            #   合理性: 追根溯源到 nav_map 数据来源混淆的根因，DOM 是唯一事实源。
            #   覆盖性: 在源头拦截，下游所有路径（_build_actions + case_rules v5 注入 + 降级用例）都受益。
            #   自我反驳:
            #     1. 是否应在 Step1 修复 nav_map 数据来源? 否，Step1 修复成本高（需重新爬取），
            #        Step3 用 DOM 校验更通用（无论 nav_map 来源如何，DOM 是唯一事实源）。
            #     2. 是否应在 case_rules v5 注入前校验? 否，case_rules 只是注入点之一，
            #        _build_actions 和降级用例也注入，应统一在源头 _find_sidebar_for_target 校验。
            #     3. 是否应完全禁止 Confluence 来源 sidebar? 否，有些系统 nav_map 来源就是 Confluence，
            #        应基于 DOM 校验而非来源标记。
            _sidebar_real_url, _sidebar_text = best_match
            if not _verify_sidebar_in_dom(_sidebar_text):
                _log.warning(
                    "[RuleExtract] ⚠️ sidebar DOM 校验失败(v7): sidebar_text='%s' 不存在于任何页面的 DOM 中 "
                    "(可能来自 Confluence 文档虚构条目)，拒绝注入 → 触发降级用例",
                    _sidebar_text,
                )
                return None
            _log.info(
                "[RuleExtract] ★ 虚拟URL匹配(v7 校验通过): req='%s' → real_url='%s' + sidebar='%s' (score=%d)",
                operation[:30],
                _sidebar_real_url[:60],
                _sidebar_text,
                best_score,
            )
            return best_match
    return None


def _verify_sidebar_in_dom(sidebar_text: str) -> bool:
    """★ 通用修复(2026-07-20 v7): 校验 sidebar_text 是否在真实 DOM 中存在。

    sidebar_text 是侧边栏菜单文本，必须在某个页面的 DOM 中真实存在
    （作为 button/menuitem/link/a 标签的文本）。

    ★ P0-1 读侧迁移(2026-08-05): page_dom_snapshots → nav_graph（read_dom_pages）。
      根因(§10.9): page_dom_snapshots 已 0 行且全仓无写入方，原查询恒返回空 →
      rows=[] → 无条件放行 → sidebar 幻觉校验形同虚设。
      现读 nav_graph_nodes（Step1 真实爬取），element_count>0 过滤逻辑保持不变。

    策略（通用，无业务硬编码）：
    1. 遍历 nav_graph 中所有有元素的页面
    2. 解析 dom_summary 中的 [PAGE_ELEMENTS] 行和 [N] tag=xxx 格式
    3. 提取所有 button/link/menuitem 的可见文本
    4. 检查 sidebar_text 是否在文本集合中（精确匹配或子串匹配）
    5. 任一页面命中即返回 True

    返回: True=sidebar 在 DOM 中存在；False=虚构条目
    """
    if not sidebar_text:
        return False
    try:
        from src.core.config_loader import config

        project = getattr(config, "project_name", "") or ""
        if not project:
            # 未配置项目 → 无法校验 → 放行（避免误伤）
            return True
        from src.pipeline.utils.nav_graph_read import read_dom_pages

        rows = [
            p for p in read_dom_pages(project) if int(p.get("element_count") or 0) > 0
        ]

        if not rows:
            # 无导航元素数据 → 无法校验 → 放行（避免误伤首次爬取前的用例）
            _log.info(
                "[RuleExtract] _verify_sidebar_in_dom: project=%s 无 nav_graph 元素数据，"
                "sidebar 幻觉校验本次放行",
                project,
            )
            return True

        # 收集所有页面的可点击元素文本
        _all_click_texts: set[str] = set()
        for row in rows:
            _summary = row.get("dom_summary") or ""
            if not _summary:
                continue
            # 解析 [PAGE_ELEMENTS] 行（Step1 爬取时汇总的真实按钮列表）
            for _line in _summary.split("\n"):
                _line = _line.strip()
                if _line.startswith("[PAGE_ELEMENTS]"):
                    # 格式: [PAGE_ELEMENTS] button: 新建|button: 删除|link: 详情|...
                    _parts = _line[len("[PAGE_ELEMENTS]") :].split("|")
                    for _p in _parts:
                        _p = _p.strip()
                        if ":" in _p:
                            _txt = _p.split(":", 1)[1].strip()
                            if _txt:
                                _all_click_texts.add(_txt)
                elif _line.startswith("[") and "]" in _line and "tag=" in _line:
                    # 格式: [N] tag=button | text=新建 | ...
                    if "tag=button" in _line or "tag=a" in _line or "tag=li" in _line:
                        # 提取 text= 或 aria-label= 或 role=menuitem 的文本
                        for _field in ("text=", "aria-label="):
                            _idx = _line.find(_field)
                            if _idx >= 0:
                                _rest = _line[_idx + len(_field) :]
                                # 取到下一个 " |" 或行尾
                                _end = _rest.find(" |")
                                _txt = _rest if _end < 0 else _rest[:_end]
                                _txt = _txt.strip()
                                if _txt:
                                    _all_click_texts.add(_txt)
                elif _line.startswith("[") and "]" in _line and "menuitem" in _line:
                    # Ant Design 菜单项: <li role="menuitem">文本</li>
                    if "text=" in _line:
                        _idx = _line.find("text=")
                        _rest = _line[_idx + len("text=") :]
                        _end = _rest.find(" |")
                        _txt = _rest if _end < 0 else _rest[:_end]
                        _txt = _txt.strip()
                        if _txt:
                            _all_click_texts.add(_txt)

        if not _all_click_texts:
            # DOM 解析失败 → 放行（避免误伤）
            _log.debug(
                "[RuleExtract] _verify_sidebar_in_dom: DOM 解析无可点击文本，放行"
            )
            return True

        # 精确匹配 + 子串匹配（双向）
        # 1. sidebar_text 在某个 DOM 文本中（sidebar 是 DOM 文本的子串）
        for _t in _all_click_texts:
            if sidebar_text in _t:
                return True
        # 2. 某个 DOM 文本在 sidebar_text 中（DOM 文本是 sidebar 的子串，如"辅材" in "辅材管理"）
        #    ★ 但要求 DOM 文本至少 2 字符，避免单字误匹配
        for _t in _all_click_texts:
            if len(_t) >= 2 and _t in sidebar_text:
                return True

        _log.info(
            "[RuleExtract] _verify_sidebar_in_dom: sidebar='%s' 不在 DOM 可点击文本集合中 "
            "(共 %d 个文本)",
            sidebar_text,
            len(_all_click_texts),
        )
        return False
    except Exception as e:
        # 放行是"无法校验"的兜底，不是"校验通过"；必须 error 级 + 堆栈，
        # 否则 sidebar 幻觉会被静默放过（AGENTS.md：禁止静默吞异常）。
        _log.error(
            "[RuleExtract] _verify_sidebar_in_dom 异常 sidebar=%r，本次放行未校验: %s",
            sidebar_text,
            e,
            exc_info=True,
        )
        return True


def _find_page_for_navigation(req: dict) -> str | None:
    """★ 治本修复 (2026-07-17): 当 target_page 为 "/" 时搜索匹配需求的页面路径。

    从 nav_graph 加载所有页面（见 `_load_page_paths_from_db`），用需求的
    module + operation 中的中文关键词匹配 dom_summary，返回最佳匹配的页面路径。

    策略（通用，无业务硬编码）：
    1. 提取需求中的中文词组（2-4字）
    2. 匹配 page_title 和 dom_summary 中的关键词
    3. 按匹配分数排序，返回最佳匹配
    4. 无匹配时返回 None

    返回: 最佳匹配的 page_path 或 None
    """
    db_pages = _load_page_paths_from_db()
    if not db_pages:
        return None
    return _match_best_page_for_req(req, db_pages)


def _score_nav_match(query: str, page_name: str) -> int:
    """★ 计算查询文本与 nav_map 页面名的匹配分数。

    匹配策略（通用，无业务硬编码）:
      1. 精确匹配 → 100
      2. 双向包含 → +20
      3. 2字子串匹配 → 每个 +3（解决"操作名→页面名"语义距离）
      4. 逐字匹配 → 每个 +1

    通用性(§12.4):
      - 跨系统/跨行业: 纯字符串操作，无分词依赖
      - 2字子串匹配对中文友好（中文词语多为2字）
    """
    if not query or not page_name:
        return 0
    if query == page_name:
        return 100
    score = 0
    # 双向包含
    if query in page_name or page_name in query:
        score += 20
    # 2字子串匹配（提取查询文本所有2字子串，检查是否出现在页面名中）
    if len(query) >= 2:
        for i in range(len(query) - 1):
            sub = query[i : i + 2]
            if sub in page_name:
                score += 3
    # 逐字匹配
    for ch in query:
        if ch in page_name:
            score += 1
    return score


def _validate_nav_hint_against_navmap(hint: str) -> str:
    """★ §12.2规则1校验: intent:xxx 必须匹配 nav_map 页面名。

    根因(§10.9 3层why):
      1. 为什么超时? → Step4 _fallback_menu_navigate 在 nav_map 搜索 intent:xxx 耗时
      2. 为什么搜索耗时? → xxx 是需求描述文本(如"3.项目本期建设目标..."), 不匹配 nav_map 页面名
      3. 为什么不匹配? → _build_nav_intent_hint 从需求文本提取关键词, 未校验 nav_map
    通用性(§12.4):
      1. 跨系统: nav_map 是通用结构
      2. 跨行业: 关键词匹配适用任何行业
      3. 降级: nav_map 查询失败 → 保留原 hint(不阻断)
    """
    if not hint or not hint.startswith("intent:"):
        return hint
    raw_text = hint[len("intent:") :].strip()
    if not raw_text:
        return "intent:首页"
    try:
        from src.core.config_loader import config

        project = getattr(config, "project_name", "") or ""
        if not project:
            return hint
        # ★ P0-3b 读侧迁移(2026-07-26): nav_graph_nodes.page_title 替 system_nav_maps.menu_groups
        #   根因(§10.9): system_nav_maps 已停写（0行），nav_graph_nodes 有真实页面标题
        from src.core.versioning import current_version_label

        _vl = current_version_label(project) if project else ""
        page_names = []
        if _vl:
            from src.pipeline.utils.nav_graph_store import read_nav_graph_nodes

            _nodes = read_nav_graph_nodes(project, _vl)
            for n in _nodes:
                _title = (n.get("page_title", "") or "").strip()
                if _title:
                    page_names.append(_title)
        if not page_names:
            return hint
        # 1. 精确匹配
        if raw_text in page_names:
            return hint
        # 2. 评分匹配（双向包含 + 2字子串 + 逐字，复用 _score_nav_match）
        best_score = 0
        best_name = ""
        for name in page_names:
            score = _score_nav_match(raw_text, name)
            if score > best_score:
                best_score = score
                best_name = name
        # 阈值提高到8（子串匹配使分数更高，需提高阈值防误匹配）
        if best_name and best_score >= 8:
            _log.info(
                "[RuleExtract] nav_map校验: '%s' → '%s' (score=%d)",
                raw_text[:30],
                best_name,
                best_score,
            )
            return f"intent:{best_name}"
        # 3. 无匹配 → 降级首页(避免超时)
        _log.warning(
            "[RuleExtract] nav_map校验: '%s' 无匹配页面 → 降级 intent:首页",
            raw_text[:30],
        )
        return "intent:首页"
    except Exception as e:
        _log.debug("[RuleExtract] nav_map校验失败: %s → 保留原hint", str(e)[:80])
        return hint


def _build_nav_intent_hint(module: str, operation: str, description: str) -> str:
    """★ 构建 navigate 的 intent: 语义提示（v2 反向匹配）。

    根因修复(§10.9):
      旧策略: 需求文本→提取关键词→生成hint→校验nav_map（命中率低，119/181降级首页）
      新策略: 直接用 module+operation 作为查询文本 → _score_nav_match 反向匹配 nav_map 页面名
      关键改进: _score_nav_match 的2字子串匹配解决"操作名→页面名"语义距离
      （如"批量导入辅材"的子串"辅材"匹配"辅材管理"）

    通用性(§12.4):
      - 跨系统/跨行业: 无业务硬编码（删除了_NAV_KEYWORDS列表），纯字符串匹配
      - 跨页面: 适用于所有导航场景
      - 降级: 无匹配 → intent:首页（不阻断）
    """
    clean_module = (module or "").strip()
    clean_op = (operation or "").strip()
    # 组合查询文本：module 优先，operation 补充（不用 description——太长会噪声）
    query = clean_module if clean_module else clean_op
    if not query:
        return "intent:首页"
    return _validate_nav_hint_against_navmap("intent:" + query)
