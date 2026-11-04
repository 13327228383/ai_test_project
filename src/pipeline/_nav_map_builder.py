# -*- coding: utf-8 -*-
"""
多源导航地图融合构建器。

从 Confluence / Figma / YApi 数据源提取导航路径，按优先级去重合并，
写入 nav_graph_nodes 表（原 system_nav_maps 纸地图已于 2026-08-05 DROP）。

提取自 step1.py 的 _build_nav_map_from_docs 函数。
"""
import json as _json
from src.core.guarded import guard
import re as _re
import logging

_log = logging.getLogger(__name__)


def build_nav_map_from_docs(project: str, version_label: str, target_url: str) -> dict:
    """多源导航地图融合构建器。

    从 5 个数据源提取导航路径，按优先级去重合并：
      需求文档(Confluence) > Figma 设计稿 > YApi 接口 > DOM 快照

    通用策略（不硬编码任何业务路径）：
    1. Confluence: 正则匹配「路径：X-Y-Z」/「X → Y → Z」模式
    2. Figma: 从 figma_pages 提取页面名，按 canvas 分组
    3. YApi: 按 API 前缀分组提取模块名，作为模块补充
    4. 融合: 同名路径高优先级覆盖低优先级，来源标注 source_priority
    5. 写入 nav_graph_nodes（结构化导航图，取代旧 system_nav_maps 纸地图）
    """
    conn = None
    try:
        from src.core.db_manager import get_connection

        _log.info(
            "[NavMap] 多源导航地图构建开始: project=%s, version=%s",
            project,
            version_label,
        )
        conn = get_connection()
        cur = conn.cursor()

        # DOM 数据充足性检查：DOM 来源已有足够真实页面时跳过多源融合（避免 Step1 超时）。
        # ★ P0-1 读侧迁移(2026-08-05): page_dom_snapshots → nav_graph_nodes。
        #   根因（承接 v5.0 未竟的迁移）:
        #     L1: 多源融合总被触发 → Step1 超时(1200s) → 流水线阻塞
        #     L2: 融合被触发 → DOM 充足性判为"不足"（_dom_valid_page_count=0）
        #     L3: 恒为 0 → 查的是 page_dom_snapshots，该表 0 行且**全仓无写入方**
        #         （v5.0 把数据源从 system_nav_maps 换成 page_dom_snapshots，
        #          但两张表都已停写，只是把死表换成了另一张死表）
        #   修复: 查 nav_graph_nodes —— DOM 爬取的真实落库产物（本模块自己写的表），
        #         过滤/去重口径完全保持不变（element_count>0、排除 /login、base_path 去重）。
        #   通用性(§12.4): nav_graph_nodes 是通用结构化导航表，跨系统/行业/页面通用。
        _MIN_VALID_PAGES_FOR_SKIP = 2
        _dom_valid_page_count = 0
        _dom_nav_json = None
        try:
            from src.pipeline.utils.nav_graph_read import read_dom_pages

            _seen_base_paths = set()
            for _sr in read_dom_pages(project):
                # ★ 排除登录页面（/login），只计算业务页面
                _u = str(_sr.get("page_url", "") or "").strip()
                if not _u or "/login" in _u:
                    continue
                if int(_sr.get("element_count") or 0) <= 0:
                    continue
                if len(str(_sr.get("dom_summary", "") or "")) <= 100:
                    continue
                # 提取 base_path: 去掉#锚点，去掉?查询参数，去掉域名
                _base = _u.split("#")[0].split("?")[0]
                if _base.startswith("http://") or _base.startswith("https://"):
                    _parts = _base.split("/", 3)
                    _base = "/" + _parts[3] if len(_parts) > 3 else "/"
                if _base and _base not in _seen_base_paths:
                    _seen_base_paths.add(_base)
                    _dom_valid_page_count += 1
            _log.info(
                "[NavMap] DOM 有效页面: %d 个 (base_path 去重)", _dom_valid_page_count
            )
        except Exception as _ce:
            _log.error(
                "[NavMap] DOM 有效页面计数失败 project=%s，将强制走多源融合(耗时): %s",
                project,
                _ce,
                exc_info=True,
            )

        if _dom_valid_page_count >= _MIN_VALID_PAGES_FOR_SKIP:
            _log.info(
                "[NavMap] DOM 来源已有足够真实页面(%d个 ≥ %d)，跳过多源融合",
                _dom_valid_page_count,
                _MIN_VALID_PAGES_FOR_SKIP,
            )
            conn.close()
            return {}
        else:
            _log.info(
                "[NavMap] DOM 来源真实页面不足(%d个 < %d)，继续多源融合",
                _dom_valid_page_count,
                _MIN_VALID_PAGES_FOR_SKIP,
            )

        base_path = target_url.rstrip("/") if target_url else ""

        # ══════════════════════════════════════════════════
        # SOURCE 1: Confluence 需求文档（Priority 1）
        # ══════════════════════════════════════════════════
        _path_patterns = [
            _re.compile(
                r"路径[：:]\s*([\u4e00-\u9fa5a-zA-Z0-9]+(?:[-–—→>]\s*[\u4e00-\u9fa5a-zA-Z0-9]+)+)"
            ),
            _re.compile(
                r"([\u4e00-\u9fa5a-zA-Z0-9]+(?:\s*[→>]\s*[\u4e00-\u9fa5a-zA-Z0-9]+){2,})"
            ),
            _re.compile(
                r"\|\s*([\u4e00-\u9fa5a-zA-Z0-9]+)\s*\|\s*([\u4e00-\u9fa5a-zA-Z0-9]+)\s*\|"
            ),
        ]

        cur.execute(
            "SELECT content FROM source_documents "
            "WHERE project=%s AND doc_type='confluence' ORDER BY updated_at DESC LIMIT 1",
            (project,),
        )
        row = cur.fetchone()
        confluence_text = row.get("content", "") if row else ""
        _log.info(
            "[NavMap] Confluence: %s, %d 字符",
            "有数据" if row else "无数据",
            len(confluence_text or ""),
        )

        # 提取路径（带来源标注）
        all_paths: list[dict] = (
            []
        )  # [{parts: [...], source: "confluence", priority: 1}]
        seen_path_keys: set[str] = set()

        for pattern in _path_patterns:
            for match in pattern.finditer(confluence_text or ""):
                path_str = match.group(1)
                parts = _re.split(r"\s*[-–—→>]\s*", path_str)
                parts = [p.strip() for p in parts if p.strip() and len(p.strip()) >= 2]
                if len(parts) >= 2:
                    path_key = "/".join(parts)
                    if path_key not in seen_path_keys:
                        seen_path_keys.add(path_key)
                        all_paths.append(
                            {"parts": parts, "source": "confluence", "priority": 1}
                        )

        _log.info("[NavMap] Confluence 提取: %d 条路径", len(all_paths))

        # ══════════════════════════════════════════════════
        # SOURCE 2: Figma 设计稿（Priority 2）
        # ══════════════════════════════════════════════════
        cur.execute(
            "SELECT name, canvas FROM figma_pages "
            "WHERE project=%s AND version_label=%s ORDER BY canvas, name",
            (project, version_label),
        )
        figma_rows = cur.fetchall() or []
        _log.info("[NavMap] Figma: %d 个页面", len(figma_rows))

        for fr in figma_rows:
            page_name = (fr.get("name", "") or "").strip()
            canvas = (fr.get("canvas", "") or "").strip()
            if not page_name or len(page_name) < 2:
                continue
            # 页面名可能含分隔符：如 "数据全局视图-烟叶数据" → ["数据全局视图", "烟叶数据"]
            # 也可能不含：如 "数据导入" → ["数据导入"]
            sub_parts = _re.split(r"\s*[-–—→>/]\s*", page_name)
            sub_parts = [
                p.strip() for p in sub_parts if p.strip() and len(p.strip()) >= 2
            ]
            if not sub_parts:
                continue
            # ★ 用页面名第一段作为模块名（而非 canvas 设计版本名），保证业务语义
            # 如 "数据全局视图-烟叶数据" → 模块="数据全局视图", 叶子="烟叶数据"
            parts = sub_parts
            path_key = "/".join(parts)
            if path_key not in seen_path_keys:
                seen_path_keys.add(path_key)
                all_paths.append({"parts": parts, "source": "figma", "priority": 2})

        _log.info(
            "[NavMap] Figma 提取: %d 条路径 (累计 %d)",
            len([p for p in all_paths if p["source"] == "figma"]),
            len(all_paths),
        )

        # ══════════════════════════════════════════════════
        # SOURCE 5: YApi 接口文档（Priority 5，最低）
        # ══════════════════════════════════════════════════
        cur.execute(
            "SELECT content FROM source_documents "
            "WHERE project=%s AND doc_type='yapi_structured' ORDER BY updated_at DESC LIMIT 1",
            (project,),
        )
        yapi_row = cur.fetchone()
        api_paths: dict[str, str] = {}  # {prefix_lower: full_path}
        api_prefix_counts: dict[str, int] = {}  # {prefix: count}
        if yapi_row and yapi_row.get("content"):
            with guard("build_nav_map_from_docs: yapi_raw = yapi_row['content']", log=_log, level=logging.WARNING):
                yapi_raw = yapi_row["content"]
                yapi_data = (
                    _json.loads(yapi_raw) if isinstance(yapi_raw, str) else yapi_raw
                )
                interfaces = (
                    yapi_data.get("interfaces", [])
                    if isinstance(yapi_data, dict)
                    else []
                )
                for api in interfaces:
                    path = api.get("path", "")
                    if path:
                        segments = [
                            s for s in path.strip("/").split("/") if s and s != "api"
                        ]
                        if segments:
                            key = segments[0].lower()
                            if key not in api_paths:
                                api_paths[key] = path
                            api_prefix_counts[key] = api_prefix_counts.get(key, 0) + 1

        _log.info("[NavMap] YApi: %d 个 API 前缀", len(api_prefix_counts))

        # ★ 通用修复(2026-07-18): YApi 接口端点不加入导航地图
        #   原因：YApi 提供的是 API 端点路径（如 /dataBoard/getTaskCount），
        #   不是 UI 导航路径。加入导航地图会污染 nav_map，导致：
        #   1. _dom_url_count 虚高，跳过 Confluence/Figma 融合
        #   2. Step3 AI 生成用例时使用 API 端点作为导航目标
        #   3. Step4 执行时导航到 API 端点返回 JSON 而非页面
        #   YApi 数据仅用于 Step4 的 API 校验，不作为导航来源。
        _log.info(
            "[NavMap] YApi: %d 个 API 前缀（不加入导航地图，仅用于Step4校验）",
            len(api_prefix_counts),
        )

        if not all_paths:
            _log.info("[NavMap] 所有数据源均未提取到路径，跳过")
            conn.close()
            return {}

        # ══════════════════════════════════════════════════
        # MERGE: 按优先级去重，高优先级覆盖低优先级
        # ══════════════════════════════════════════════════
        # 去重键：最后一级路径段（leaf name）
        merged_by_leaf: dict[str, dict] = {}
        for p in all_paths:
            parts = p["parts"]
            leaf = parts[-1] if parts else ""
            if not leaf:
                continue
            if (
                leaf not in merged_by_leaf
                or p["priority"] < merged_by_leaf[leaf]["priority"]
            ):
                merged_by_leaf[leaf] = p

        _log.info(
            "[NavMap] 去重后: %d 条路径 (%d 去重)",
            len(merged_by_leaf),
            len(all_paths) - len(merged_by_leaf),
        )

        # ══════════════════════════════════════════════════
        # BUILD: 构建导航地图结构
        # ══════════════════════════════════════════════════
        nav_map: dict = {
            "root_url": base_path,
            "total_links": 0,
            "menu_groups": [],
            "flat_list": [],
            "top_nav_modules": [],
        }

        # 按第一级分组
        groups: dict[str, list[dict]] = {}
        for leaf, p in merged_by_leaf.items():
            parts = p["parts"]
            group_name = parts[0] if parts else "未分类"
            item_text = leaf
            parent_path = "/".join(parts[:-1]) if len(parts) > 2 else group_name

            # 尝试匹配 API 路径（用于 URL 填充）
            item_url = ""
            item_url_path = ""
            if p.get("api_path"):
                item_url = base_path + p["api_path"] if base_path else p["api_path"]
                item_url_path = p["api_path"]
            else:
                for seg in parts:
                    seg_lower = seg.lower().replace("管理", "").replace(" ", "")
                    for api_key, api_path_val in api_paths.items():
                        if seg_lower in api_key or api_key in seg_lower:
                            item_url = (
                                base_path + api_path_val if base_path else api_path_val
                            )
                            item_url_path = api_path_val
                            break
                    if item_url:
                        break

            if group_name not in groups:
                groups[group_name] = []
            groups[group_name].append(
                {
                    "text": item_text,
                    "url": item_url,
                    "path": item_url_path,
                    "parent_path": parent_path,
                    "source": p["source"],
                    "priority": p["priority"],
                }
            )

        for group_name, items in groups.items():
            nav_map["top_nav_modules"].append(group_name)
            nav_map["menu_groups"].append(
                {
                    "group_name": group_name,
                    "item_count": len(items),
                    "items": items,
                }
            )
            nav_map["total_links"] += len(items)
            for item in items:
                nav_map["flat_list"].append(
                    {
                        "text": item["text"],
                        "url": item["url"],
                        "path": item["path"],
                    }
                )

        _log.info(
            "[NavMap] 导航地图: %d 个模块, %d 个链接",
            len(groups),
            nav_map["total_links"],
        )

        # ══════════════════════════════════════════════════
        # WRITE: 写入 nav_graph_nodes（与 DOM 来源合并；原 system_nav_maps 已于 2026-08-05 DROP）
        # ══════════════════════════════════════════════════
        if nav_map["total_links"] > 0:
            # ★ 通用修复 (2026-07-18 v2): 合并 DOM 已捕获的 URL 和 page_elements
            #   DOM 即使只有少量 URL，也是真实URL，应合并到 Confluence/Figma 构建的地图中
            # ★ 通用修复(2026-07-20 v2): 保留虚拟URL的 sidebar_text/real_url/page_elements 元数据
            #   原因: 某SPA系统侧边栏菜单点击后URL不变，合成虚拟URL（含#sidebar=）后，
            #         Step3需要 sidebar_text 生成 click(sidebar) action，需要 real_url 生成 navigate action。
            if _dom_nav_json is not None:
                with guard("build_nav_map_from_docs: dom_nav = _dom_nav_json", log=_log, level=logging.WARNING):
                    dom_nav = _dom_nav_json
                    # 合并 DOM 的 flat_list（DOM 有真实 URL，优先级更高）
                    dom_leaves = {
                        item.get("text", ""): item
                        for item in dom_nav.get("flat_list", [])
                    }
                    # ★ 通用修复(2026-07-18 v3): 同时按最后一段路径名建立索引
                    dom_leaves_by_last = {}
                    for dl_text, dl_item in dom_leaves.items():
                        _last = (
                            dl_text.rsplit("/", 1)[-1].strip()
                            if "/" in dl_text
                            else dl_text
                        )
                        if _last and _last not in dom_leaves_by_last:
                            dom_leaves_by_last[_last] = dl_item

                    def _copy_dom_metadata(target_item: dict, source_dom: dict) -> None:
                        """复制DOM项的URL、path、page_elements、sidebar_text、real_url到目标项。

                        ★ 通用修复(2026-07-20 v8): 合并 DOM 数据时标注 source='dom'
                          原bug: DOM 真实URL被合并到 confluence/figma item 后，source 字段不变，
                                 导致 nav_map 中所有 item 的 source 都是 confluence/figma，
                                 无法区分哪些条目有真实DOM数据。
                          修复: 合并 DOM 数据时，source 改为 'dom'，标识该条目有真实DOM支撑。
                          通用性: 适用于所有多源融合场景，让 Step3 能优先使用 DOM 支撑的条目。
                          合理性: 追根溯源到"DOM 数据被吞没"的缺陷，保留 DOM 来源标识。
                          覆盖性: 覆盖所有 _copy_dom_metadata 调用点（flat_list + menu_groups）。
                        """
                        if not target_item.get("url"):
                            target_item["url"] = source_dom.get("url", "")
                            target_item["path"] = source_dom.get("path", "")
                        elif not target_item.get("path"):
                            target_item["path"] = source_dom.get("path", "")
                        if source_dom.get("page_elements"):
                            target_item["page_elements"] = source_dom["page_elements"]
                        # ★ 通用修复(2026-07-20 v2): 保留虚拟URL元数据
                        if source_dom.get("sidebar_text"):
                            target_item["sidebar_text"] = source_dom["sidebar_text"]
                        if source_dom.get("real_url"):
                            target_item["real_url"] = source_dom["real_url"]
                        # ★ v8: 标注 source='dom'，标识该条目有真实DOM支撑
                        target_item["source"] = "dom"

                    for item in nav_map["flat_list"]:
                        if item["text"] in dom_leaves and dom_leaves[item["text"]].get(
                            "url"
                        ):
                            _copy_dom_metadata(item, dom_leaves[item["text"]])
                        elif not item.get("url"):
                            _item_last = (
                                item["text"].rsplit("/", 1)[-1].strip()
                                if "/" in item["text"]
                                else item["text"]
                            )
                            if _item_last in dom_leaves_by_last and dom_leaves_by_last[
                                _item_last
                            ].get("url"):
                                _copy_dom_metadata(item, dom_leaves_by_last[_item_last])
                    # ★ 同步更新 menu_groups 中的 URL 和 page_elements
                    for mg in nav_map.get("menu_groups", []):
                        for m_item in mg.get("items", []):
                            if m_item["text"] in dom_leaves and dom_leaves[
                                m_item["text"]
                            ].get("url"):
                                _copy_dom_metadata(m_item, dom_leaves[m_item["text"]])
                            elif not m_item.get("url"):
                                _m_last = (
                                    m_item["text"].rsplit("/", 1)[-1].strip()
                                    if "/" in m_item["text"]
                                    else m_item["text"]
                                )
                                if _m_last in dom_leaves_by_last and dom_leaves_by_last[
                                    _m_last
                                ].get("url"):
                                    _copy_dom_metadata(
                                        m_item, dom_leaves_by_last[_m_last]
                                    )
                    # ★ 通用修复(2026-07-19 → v3 2026-07-20): 按 group 名称匹配 DOM URL
                    #   v3 修复根因: 当 fallback_dom 是虚拟URL（含 sidebar_text）时，
                    #     原逻辑把父菜单的 sidebar_text 错误复制到所有子项，导致 Step3 生成
                    #     click("父菜单名") 而非 click("子菜单名")，无法到达目标子菜单页面。
                    #   修复策略: 虚拟URL场景下，为每个子项合成自己的虚拟URL：
                    #     - real_url = 父菜单的 real_url
                    #     - sidebar_text = 子项自己的 text
                    #     - url = real_url#sidebar={子项text}
                    #   通用性: 适用于所有"侧边栏菜单点击后URL不变"的SPA系统。
                    #   合理性: 追根溯源到 fallback 错误复制父菜单 sidebar_text 的缺陷。
                    #   覆盖性: 兼容虚拟URL（子项合成自己的URL）和真实URL（沿用父菜单URL）两种场景。
                    _group_url_fallback = 0
                    _virtual_synthesized = 0
                    for mg in nav_map.get("menu_groups", []):
                        _gname = mg.get("group_name", "")
                        if _gname in dom_leaves and dom_leaves[_gname].get("url"):
                            _fallback_dom = dom_leaves[_gname]
                            _fallback_is_virtual = bool(
                                _fallback_dom.get("sidebar_text")
                            )
                            _fallback_real_url = _fallback_dom.get("real_url", "") or ""
                            for m_item in mg.get("items", []):
                                if not m_item.get("url"):
                                    if _fallback_is_virtual and _fallback_real_url:
                                        # ★ 虚拟URL场景: 为子项合成自己的虚拟URL
                                        _child_text = (
                                            m_item.get("text", "") or ""
                                        ).strip()
                                        if _child_text:
                                            m_item["url"] = (
                                                f"{_fallback_real_url}#sidebar={_child_text}"
                                            )
                                            m_item["path"] = (
                                                f"{_fallback_dom.get('path', '').split('#')[0]}#sidebar={_child_text}"
                                            )
                                            m_item["sidebar_text"] = _child_text
                                            m_item["real_url"] = _fallback_real_url
                                            if _fallback_dom.get("page_elements"):
                                                m_item["page_elements"] = _fallback_dom[
                                                    "page_elements"
                                                ]
                                            _virtual_synthesized += 1
                                    else:
                                        # ★ 真实URL场景: 沿用父菜单URL（原逻辑）
                                        m_item["url"] = _fallback_dom["url"]
                                        m_item["path"] = _fallback_dom.get("path", "")
                                        if _fallback_dom.get("page_elements"):
                                            m_item["page_elements"] = _fallback_dom[
                                                "page_elements"
                                            ]
                                        if _fallback_dom.get("sidebar_text"):
                                            m_item["sidebar_text"] = _fallback_dom[
                                                "sidebar_text"
                                            ]
                                        if _fallback_dom.get("real_url"):
                                            m_item["real_url"] = _fallback_dom[
                                                "real_url"
                                            ]
                                    _group_url_fallback += 1
                        for item in nav_map["flat_list"]:
                            if not item.get("url"):
                                for m_item in mg.get("items", []):
                                    if item["text"] == m_item.get(
                                        "text", ""
                                    ) and m_item.get("url"):
                                        item["url"] = m_item["url"]
                                        item["path"] = m_item.get("path", "")
                                        if m_item.get("sidebar_text"):
                                            item["sidebar_text"] = m_item[
                                                "sidebar_text"
                                            ]
                                        if m_item.get("real_url"):
                                            item["real_url"] = m_item["real_url"]
                    if _group_url_fallback:
                        _log.info(
                            "[NavMap] 按 group 名称匹配: %d 个子项获得 fallback URL "
                            "(其中虚拟URL合成: %d)",
                            _group_url_fallback,
                            _virtual_synthesized,
                        )
                    # ★ 通用修复(2026-07-19): 将未匹配的 DOM URL 追加到 flat_list
                    # ★ 通用修复(2026-07-20 v2): 同时追加虚拟URL及其元数据
                    _unmatched_dom = 0
                    _all_dom_items = {}  # text → dom_item（含虚拟URL元数据）
                    for _dom_item in dom_nav.get("flat_list", []):
                        _dom_text = (_dom_item.get("text", "") or "").strip()
                        if _dom_text and _dom_text not in _all_dom_items:
                            _all_dom_items[_dom_text] = _dom_item
                    for _mg in dom_nav.get("menu_groups", []):
                        for _mg_item in _mg.get("items", []):
                            _mg_text = (
                                _mg_item.get("text", "")
                                or _mg_item.get("name", "")
                                or ""
                            ).strip()
                            if _mg_text and _mg_text not in _all_dom_items:
                                _all_dom_items[_mg_text] = _mg_item
                    for _dom_text, _dom_item in _all_dom_items.items():
                        _dom_url = _dom_item.get("url", "") or _dom_item.get("path", "")
                        if not _dom_url:
                            continue
                        _already_in = any(
                            (
                                item.get("url") == _dom_url
                                or item.get("path") == _dom_url
                            )
                            for item in nav_map["flat_list"]
                        )
                        if not _already_in:
                            _new_item = {
                                "text": _dom_text,
                                "url": _dom_url,
                                "path": _dom_item.get("path", _dom_url),
                                # ★ v8: 标注 source='dom'，标识该条目来自 DOM 爬取
                                "source": "dom",
                            }
                            # ★ 保留虚拟URL元数据
                            if _dom_item.get("page_elements"):
                                _new_item["page_elements"] = _dom_item["page_elements"]
                            if _dom_item.get("sidebar_text"):
                                _new_item["sidebar_text"] = _dom_item["sidebar_text"]
                            if _dom_item.get("real_url"):
                                _new_item["real_url"] = _dom_item["real_url"]
                            nav_map["flat_list"].append(_new_item)
                            nav_map["total_links"] += 1
                            _unmatched_dom += 1
                    if _unmatched_dom:
                        _log.info(
                            "[NavMap] 追加 %d 个未匹配的 DOM URL 到 flat_list",
                            _unmatched_dom,
                        )
                    _log.info(
                        "[NavMap] 已合并 DOM 来源的 %d 个URL", len(_all_dom_items)
                    )

            # ★ P0-3b 废弃侧修复(2026-07-24): 停写 system_nav_maps（纸地图）
            #   根因: system_nav_maps 是纸地图（非真实爬取），P0-3b 要求迁移到 nav_graph_nodes。
            #         读取侧已切换（_rule_extractor._load_nav_flat_list 优先读 nav_graph），
            #         写入侧仍双写 → 数据冗余 + Step3 降级读到旧纸地图数据 → P0-3b 未闭环。
            #   修复: 删除 system_nav_maps 的 DELETE + INSERT，只写 nav_graph_nodes（见下方）。
            #   通用性: 跨系统/跨行业/跨页面通用，nav_graph_nodes 是标准结构化存储。
            #   合规: AGENTS.md §10.5 零兼容负担 — 废弃表直接停写，不保留兼容写入。
            #   降级: system_nav_maps 旧数据保留（不 DELETE），nav_graph 为空时 Step3 降级可读。

        conn.close()

        # ★ P0-3b: 写结构化 nav_graph（替纸地图 system_nav_maps，已停写）
        #   Step3 优先读 nav_graph_nodes（system_nav_maps 已于 2026-08-05 物理 DROP，无降级保留）
        if nav_map.get("flat_list"):
            with guard("build_nav_map_from_docs: from src.pipeline.utils.nav_graph_st", log=_log, level=logging.WARNING):
                from src.pipeline.utils.nav_graph_store import write_nav_graph

                _ng = write_nav_graph(
                    project,
                    version_label,
                    nav_map.get("flat_list", []),
                    nav_map.get("menu_groups", []),
                )
                if _ng.get("success"):
                    _log.info(
                        "[NavMap] ✓ nav_graph 已写入: %d nodes, %d edges",
                        _ng.get("nodes", 0),
                        _ng.get("edges", 0),
                    )
                else:
                    _log.warning(
                        "[NavMap] nav_graph 写入失败(不阻断): %s", _ng.get("error", "")
                    )

        return nav_map

    except Exception as e:
        import traceback as _tb

        _log.warning("[NavMap] 多源导航地图构建异常(不阻塞): %s", str(e)[:200])
        _log.warning("[NavMap] 堆栈: %s", _tb.format_exc()[-300:])
        with guard("build_nav_map_from_docs: if conn: conn.close()", log=_log):
            if conn:
                conn.close()
        return {}
