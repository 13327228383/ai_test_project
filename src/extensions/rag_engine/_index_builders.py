# -*- coding: utf-8 -*-
"""RAG 索引构建器模块 — 从 engine_core.py 拆分（§4.3 单文件≤800行）。

职责：封装 build_index 的各数据源 chunk 构建逻辑为模块级函数。
  - _build_dom_chunks: DOM 快照分块
  - _build_api_chunks: YApi 接口分块
  - _build_db_chunks: DB Schema 分块（表摘要+字段级+数据样例）
  - _build_feature_chunks: 功能清单分块（功能项级+页面级摘要）
  - _build_prototype_chunks: 原型图识别结果分块
  - _build_figma_index: Figma 索引构建（pages/nodes/components）

被 engine_core.RagEngine.build_index 调用（self 通过参数传递，参考 _ai_loop_phases.py 模式）。

根因(3层追问):
  1. 表层: engine_core.py 946 行超 §4.3 红线
  2. 中层: build_index 内嵌 5 个闭包 + _build_figma_index 方法
  3. 根因: 闭包本质是模块级函数+self参数，未按"文件按功能分文件夹"约束拆分
通用性: 适用于所有系统/行业 — 纯 chunk 构建逻辑，不依赖业务术语
覆盖性: 影响所有 RAG 索引构建路径（dom/api/db/feature/prototype/figma）
"""
import hashlib
import json
import logging
from collections import Counter

_log = logging.getLogger("rag_engine")


# ── DOM 快照分块 ────────────────────────────────────────────────────────
def _build_dom_chunks(self, dom_snapshots) -> list:
    """从 DOM 快照构建元素级 chunk。

    Args:
        self: RagEngine 实例（访问 _extract_keywords）
        dom_snapshots: Step1 DOM 爬取结果列表

    Returns:
        list[dict]: DOM 元素级 chunk 列表
    """
    if not dom_snapshots:
        return []
    chunks = []
    for snap in dom_snapshots:
        page_path = snap.get("page_path", "")
        page_title = snap.get("page_title", "")
        page_url = snap.get("page_url", "")
        dom_summary = snap.get("dom_summary", "")
        if not dom_summary:
            continue
        for line in dom_summary.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("[") and "]" in line:
                el_idx = line.split("]")[0][1:]
                chunk_id = (
                    f"dom:{page_path}:{el_idx}"
                    if page_path
                    else f"dom:{page_url[:50]}:{el_idx}"
                )
                text = f"[页面:{page_title}] 路径:{page_path}\n元素:{line}"
                chunks.append(
                    {
                        "type": "dom",
                        "chunk_id": chunk_id,
                        "text": text,
                        "meta": json.dumps(
                            {
                                "page_url": page_url,
                                "page_path": page_path,
                                "page_title": page_title,
                                "element_line": line,
                            },
                            ensure_ascii=False,
                        ),
                        "keywords": self._extract_keywords(text),
                        "entity_refs": [{"type": "dom", "chunk_id": chunk_id}],
                    }
                )
    _log.info("[RagEngine] DOM 快照分块完成: %d 个元素级chunk", len(chunks))
    return chunks


# ── YApi 接口分块 ───────────────────────────────────────────────────────
def _build_api_chunks(self, yapi_specs) -> list:
    """从 YApi 接口文档构建 API chunk。

    Args:
        self: RagEngine 实例（访问 _parse_yapi_endpoints/_format_api_chunk/_extract_keywords）
        yapi_specs: YApi 接口文档字典

    Returns:
        list[dict]: API chunk 列表
    """
    if not yapi_specs:
        return []
    chunks = []
    for ep in self._parse_yapi_endpoints(yapi_specs):
        text = self._format_api_chunk(ep)
        chunk_id = f"api:{ep.get('method','?')}:{ep.get('path','?')}"
        chunks.append(
            {
                "type": "api",
                "chunk_id": chunk_id,
                "text": text,
                "meta": json.dumps(ep, ensure_ascii=False),
                "keywords": self._extract_keywords(text),
                # ★ v4: entity_refs 供 EntityResolver 精确查询源数据
                "entity_refs": [{"type": "api", "chunk_id": chunk_id}],
            }
        )
    _log.info("[RagEngine] API 分块完成: %d", len(chunks))
    return chunks


# ── DB Schema 分块（表摘要+字段级+数据样例）─────────────────────────────
def _build_db_chunks(self, db_schema) -> list:
    """从 DB Schema 构建 chunk（表摘要 + 字段级 + 数据样例）。

    ★ v5: 拆分为字段级chunk + 表摘要chunk，而非整表一个chunk
       字段级：每个字段一个chunk（搜索"用户名字段"能直接命中）
       表摘要：表名+字段列表+外键关系（搜索"用户表"能命中整体结构）
       数据样例：每个表的样例数据（搜索"测试数据"能命中）

    Args:
        self: RagEngine 实例（访问 _parse_db_tables/_format_db_chunk/_extract_keywords）
        db_schema: DB Schema 文本

    Returns:
        list[dict]: DB chunk 列表（db_table/db_column/db_sample 三类）
    """
    chunks = []
    for table in self._parse_db_tables(db_schema):
        tname = table.get("name", "?")
        col_details = table.get("col_details") or []
        columns = table.get("columns") or [c.get("name", "") for c in col_details]
        foreign_keys = table.get("foreign_keys") or []

        # --- 表摘要chunk（整体结构，用于表级检索）---
        table_summary = self._format_db_chunk(table)
        chunks.append(
            {
                "type": "db_table",
                "chunk_id": f"db:{tname}",
                "text": f"[表:{tname}] {table_summary}",
                "meta": json.dumps(table, ensure_ascii=False),
                "keywords": self._extract_keywords(table_summary),
                "entity_refs": [
                    {"type": "db_table", "id": tname, "chunk_id": f"db:{tname}"}
                ],
            }
        )

        # --- 字段级chunk（每个字段一个chunk，用于字段级精准检索）---
        for col in col_details:
            cname = col.get("name", col.get("COLUMN_NAME", ""))
            if not cname:
                continue
            ctype = col.get("type", col.get("DATA_TYPE", ""))
            ccomment = col.get("comment", col.get("COLUMN_COMMENT", ""))
            ckey = col.get("column_key", col.get("COLUMN_KEY", ""))
            is_pk = "PRI" in str(ckey)
            col_text = f"[表:{tname}] 字段:{cname}"
            if ctype:
                col_text += f" 类型:{ctype}"
            if is_pk:
                col_text += " [主键]"
            if ccomment:
                col_text += f" 注释:{ccomment}"
            # 外键信息
            for fk in foreign_keys:
                if fk.get("column") == cname:
                    col_text += (
                        f" [外键→{fk.get('ref_table', '')}.{fk.get('ref_column', '')}]"
                    )
            chunks.append(
                {
                    "type": "db_column",
                    "chunk_id": f"db_col:{tname}.{cname}",
                    "text": col_text,
                    "meta": json.dumps(
                        {
                            "table": tname,
                            "column": cname,
                            "type": ctype,
                            "comment": ccomment,
                            "is_pk": is_pk,
                        },
                        ensure_ascii=False,
                    ),
                    "keywords": self._extract_keywords(col_text),
                    "entity_refs": [
                        {"type": "db_table", "id": tname, "chunk_id": f"db:{tname}"},
                        {
                            "type": "db_column",
                            "id": f"{tname}.{cname}",
                            "chunk_id": f"db_col:{tname}.{cname}",
                        },
                    ],
                }
            )

        # --- 数据样例chunk（如果有sample_data）---
        sample_data = table.get("sample_data") or []
        if sample_data:
            sample_lines = [f"[表:{tname}] 测试数据样例:"]
            for i, row in enumerate(sample_data[:5]):  # 最多5行样例
                sample_lines.append(
                    f"  {i+1}. {json.dumps(row, ensure_ascii=False)[:200]}"
                )
            sample_text = "\n".join(sample_lines) + "\n"
            chunks.append(
                {
                    "type": "db_sample",
                    "chunk_id": f"db_sample:{tname}",
                    "text": sample_text,
                    "meta": json.dumps(
                        {"table": tname, "sample_count": len(sample_data)},
                        ensure_ascii=False,
                    ),
                    "keywords": self._extract_keywords(sample_text),
                    "entity_refs": [
                        {"type": "db_table", "id": tname, "chunk_id": f"db:{tname}"}
                    ],
                }
            )

    _log.info(
        "[RagEngine] DB 分块完成: %d (表:%d, 字段:%d, 样例:%d)",
        len(chunks),
        sum(1 for c in chunks if c["type"] == "db_table"),
        sum(1 for c in chunks if c["type"] == "db_column"),
        sum(1 for c in chunks if c["type"] == "db_sample"),
    )
    return chunks


# ── 功能清单分块（功能项级+页面级摘要）──────────────────────────────────
def _build_feature_chunks(self, project: str = "", version_label: str = "") -> list:
    """从 system_feature_list 表构建功能清单 RAG 索引。

    分层拆分（符合项目约定）：
      1. 页面级摘要 chunk（feature_page_summary）— 每页一个，含功能项总数/类型分布/主要功能标签
      2. 功能项级 chunk（feature）— 每个功能项一个，用于细粒度匹配

    优化点：
      - chunk_id 用 page_path+feat_text 哈希（跨版本稳定，不再依赖 DB 自增主键）
      - selector 移到 meta（不污染 text 语义检索）
      - feat_text 多字段拼接（name/type/description）提升语义丰富度

    Args:
        self: RagEngine 实例（访问 _extract_keywords）
        project / version_label: 由 build_index 入口线程解析后传入（worker 线程
            thread-local 为空，直接走 config 兜底会拿到空/默认值，导致查 0 行）。

    Returns:
        list[dict]: 功能清单 chunk 列表（feature/feature_page_summary 两类）
    """
    chunks = []
    conn = None
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        # ★ 通用性修复(2026-09-01): 与 _build_figma_index 一致，优先用入口线程传入的
        #   project/version_label，而非在 worker 线程里回退 config（thread-local 空）。
        _project = (project or self._get_project_name() or "").strip()
        _ver = (version_label or self._get_project_version() or "").strip()
        rows = (
            conn.execute(
                "SELECT id, page_path, page_title, features_json "
                "FROM system_feature_list WHERE project = %s AND version_label = %s",
                (_project, _ver),
            ).fetchall()
            if _project
            else []
        )
        if not rows:
            _log.debug("[RagEngine] 功能清单为空，跳过")
            return []
        for row in rows:
            features = []
            try:
                features = json.loads(row.get("features_json", "[]") or "[]")
            except Exception as _e:
                _log.debug("features_json 解析失败: %s", _e, exc_info=True)
            if not isinstance(features, list):
                continue
            page_title = row.get("page_title", "") or ""
            page_path = row.get("page_path", "") or ""
            row_id = row.get("id", 0) or 0

            # ── 功能项级 chunk ──
            type_dist = Counter()  # 页面级摘要用：类型分布
            feat_labels = []  # 页面级摘要用：主要功能标签
            for fi, feat in enumerate(features):
                if not isinstance(feat, dict):
                    continue
                feat_type = feat.get("type", "") or ""
                # ★ 多字段拼接提升语义丰富度（与 _build_feature_search_text 一致）
                feat_name = (
                    feat.get("name", "")
                    or feat.get("text", "")
                    or feat.get("label", "")
                    or ""
                )
                if not feat_name:
                    continue
                feat_desc = feat.get("description", "") or ""
                # text 不含 selector（移到 meta），保留语义文本
                text = f"[{page_title}] {feat_type}: {feat_name}"
                if feat_desc and feat_desc != feat_name:
                    text += f" — {feat_desc}"
                selector = feat.get("selector", "") or ""
                # ★ chunk_id 用 page_path+feat_name 哈希（跨版本稳定）
                _stable_key = f"{page_path}|{feat_type}|{feat_name}"
                _chunk_hash = hashlib.md5(_stable_key.encode("utf-8")).hexdigest()[:12]
                chunks.append(
                    {
                        "type": "feature",
                        "chunk_id": f"feature:{_chunk_hash}",
                        "text": text,
                        "meta": json.dumps(
                            {
                                "feature_id": row_id,
                                "page_path": page_path,
                                "page_title": page_title,
                                "feature_type": feat_type,
                                "feature_name": feat_name,
                                "selector": selector,
                                "feat_index": fi,
                            },
                            ensure_ascii=False,
                        ),
                        "keywords": self._extract_keywords(text),
                    }
                )
                # 累计页面级统计
                if feat_type:
                    type_dist[feat_type] += 1
                feat_labels.append(feat_name)

            # ── 页面级摘要 chunk ──
            if features:
                dist_str = "、".join(
                    f"{t}:{c}"
                    for t, c in sorted(type_dist.items(), key=lambda x: -x[1])
                )
                top_labels = "、".join(feat_labels[:8])
                summary_text = f"[页面摘要] {page_title} ({page_path}) — 共 {len(feat_labels)} 个功能项，类型分布: {dist_str}，主要功能: {top_labels}"
                _summary_key = f"{page_path}|summary"
                _summary_hash = hashlib.md5(_summary_key.encode("utf-8")).hexdigest()[
                    :12
                ]
                chunks.append(
                    {
                        "type": "feature_page_summary",
                        "chunk_id": f"feature_summary:{_summary_hash}",
                        "text": summary_text,
                        "meta": json.dumps(
                            {
                                "page_path": page_path,
                                "page_title": page_title,
                                "feature_count": len(feat_labels),
                                "type_distribution": type_dist,
                            },
                            ensure_ascii=False,
                        ),
                        "keywords": self._extract_keywords(summary_text),
                    }
                )
        _log.info("[RagEngine] 功能清单分块完成: %d (含页面摘要)", len(chunks))
    except Exception as e:
        _log.warning("[RagEngine] 功能清单分块失败: %s", str(e)[:120])
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)
    return chunks


# ── 原型图识别结果分块 ──────────────────────────────────────────────────
def _build_prototype_chunks(self, prototype_images) -> list:
    """从视觉模型识别结果构建原型图 RAG 索引。

    每张原型图一个 chunk，包含页面名称、控件清单、表格字段、状态值等。

    Args:
        self: RagEngine 实例（访问 _extract_keywords）
        prototype_images: 原型图识别结果列表（来自 Step1 视觉识别）

    Returns:
        list[dict]: 原型图 chunk 列表
    """
    if not prototype_images:
        return []
    chunks = []
    for idx, img in enumerate(prototype_images):
        text = img.get("text", "")
        if not text:
            continue
        source = img.get("source", "unknown")
        url = img.get("url", "")
        chunk_id = f"prototype:{source}:{url[:40]}:{idx}"
        chunks.append(
            {
                "type": "prototype_image",
                "chunk_id": chunk_id,
                "text": text,
                "meta": json.dumps(
                    {
                        "source": source,
                        "url": url,
                        "index": idx,
                    },
                    ensure_ascii=False,
                ),
                "keywords": self._extract_keywords(text),
                "entity_refs": [{"type": "prototype_image", "chunk_id": chunk_id}],
            }
        )
    _log.info("[RagEngine] 原型图分块完成: %d chunks", len(chunks))
    return chunks


# ── Figma 索引构建（pages/nodes/components）─────────────────────────────
def _build_figma_index(self, project: str = "", version_label: str = "") -> list:
    """从 figma_pages / figma_nodes / figma_components 表读取并建立 RAG 索引。

    返回: chunk 列表（v3: 返回列表而非直接追加到 self._chunks，支持并行组装）。
    ★ v2: 智能分段替代硬截断，长页面/节点拆为多个 chunk。

    Args:
        self: RagEngine 实例（访问 _get_project_name/_get_project_version/
              _segment_figma_page/_smart_segment/_extract_keywords）
        project / version_label: 由 build_index 入口线程解析后传入（worker 线程
            thread-local 为空，config 兜底拿不到当前项目）。

    Returns:
        list[dict]: Figma chunk 列表（figma_page/figma_node/figma_component 三类）
    """
    # ★ 通用性修复(2026-09-01): 优先用入口线程传入的 project/version_label，
    #   而非在 worker 线程里重走 _get_project_name()（thread-local 空 → 查 0 行）。
    project = (project or self._get_project_name() or "").strip()
    version_label = (version_label or self._get_project_version() or "").strip()
    chunks = []

    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            # 1. figma_pages — 智能分段：先按功能区域分组，失败再按段落拆分
            pages = conn.execute(
                "SELECT page_id, name, canvas, page_type, ui_structure, element_count "
                "FROM figma_pages WHERE project=%s AND version_label=%s",
                (project, version_label),
            ).fetchall()
            for p in pages or []:
                ui_text = p.get("ui_structure") or ""
                if ui_text:
                    # ★ v6: 超长页面用统一功能点提取器生成摘要，禁止硬截断
                    # 适用 extract_feature_points：模拟人类提炼UI功能点
                    if len(ui_text) > 3000:
                        try:
                            from src.extensions.rag_engine.chunking_mixin_mixins._segment import (
                                extract_feature_points,
                            )

                            summary = extract_feature_points(
                                ui_text, max_chars=2000, focus_keyword=p.get("name", "")
                            )
                            if summary:
                                segments = [summary]
                                _log.debug(
                                    "[RagEngine] Figma页面 %s 过长(%d字)，提取功能点摘要",
                                    p.get("name", "?"),
                                    len(ui_text),
                                )
                            else:
                                segments = self._segment_figma_page(
                                    ui_text, max_len=1500
                                )
                        except Exception as _e:
                            _log.debug(
                                "[RagEngine] Figma功能点提取失败: %s", str(_e)[:80]
                            )
                            segments = self._segment_figma_page(ui_text, max_len=1500)
                    else:
                        # ★ v5: 优先按功能区域/分组拆分（模拟人类理解页面结构）
                        segments = self._segment_figma_page(ui_text, max_len=1500)
                else:
                    segments = [""]
                base_meta = json.dumps(
                    {
                        "name": p["name"],
                        "canvas": p.get("canvas", ""),
                        "page_type": p.get("page_type", ""),
                        "element_count": p.get("element_count", 0),
                    },
                    ensure_ascii=False,
                )
                for seg_idx, seg_text in enumerate(segments):
                    if not seg_text.strip():
                        continue
                    header = f"页面: {p['name']}"
                    if p.get("canvas"):
                        header += f"\n所属画布: {p['canvas']}"
                    if len(segments) > 1:
                        header += f" [{seg_idx + 1}/{len(segments)}]"
                    text = header + "\n" + seg_text
                    chunk_id = f"figma_page:{p['page_id']}"
                    if len(segments) > 1:
                        chunk_id += f":{seg_idx}"
                    chunks.append(
                        {
                            "type": "figma_page",
                            "chunk_id": chunk_id,
                            "text": text,
                            "meta": base_meta,
                            "keywords": self._extract_keywords(text),
                        }
                    )

            # 2. figma_nodes — 每条节点一个 chunk，带完整功能路径（画布 > 页面 > 节点）
            # ★ v3: JOIN figma_pages 获取画布/页面名，构建层级功能路径
            nodes = conn.execute(
                "SELECT n.node_id, n.page_id, n.name, n.node_type, n.text_content, n.meta_json, "
                "COALESCE(p.name, '') as page_name, COALESCE(p.canvas, '') as canvas "
                "FROM figma_nodes n LEFT JOIN figma_pages p ON n.page_id = p.page_id "
                "AND n.project = p.project AND n.version_label = p.version_label "
                "WHERE n.project=%s AND n.version_label=%s "
                "AND (n.text_content IS NOT NULL OR (n.name IS NOT NULL AND n.name != '')) "
                "ORDER BY p.canvas, p.name, n.name "
                "LIMIT 800",
                (project, version_label),
            ).fetchall()
            for n in nodes or []:
                raw_text = n.get("text_content") or ""
                node_name = n.get("name") or ""
                page_name = n.get("page_name") or ""
                canvas = n.get("canvas") or ""
                # 构建功能路径: 画布 > 页面 > 节点名
                path_parts = [p for p in [canvas, page_name, node_name] if p]
                func_path = " > ".join(path_parts) if path_parts else node_name
                if raw_text:
                    segments = self._smart_segment(raw_text, max_len=500)
                else:
                    segments = [f"功能路径: {func_path}"]
                meta = {
                    "name": node_name,
                    "node_type": n.get("node_type", ""),
                    "page_id": n.get("page_id", ""),
                    "page_name": page_name,
                    "canvas": canvas,
                }
                meta_json_str = n.get("meta_json")
                if meta_json_str:
                    try:
                        if isinstance(meta_json_str, str):
                            extra = json.loads(meta_json_str)
                        else:
                            extra = meta_json_str
                        meta.update(extra)
                    except Exception:
                        _log.debug(
                            "[RagEngine] figma node meta_json 解析失败: %s",
                            str(meta_json_str)[:80],
                            exc_info=True,
                        )
                base_meta = json.dumps(meta, ensure_ascii=False)
                for seg_idx, seg_text in enumerate(segments):
                    if not seg_text.strip():
                        continue
                    parts = []
                    if canvas:
                        parts.append(f"画布: {canvas}")
                    if page_name:
                        parts.append(f"页面: {page_name}")
                    parts.append(f"节点: {node_name}")
                    if n.get("node_type"):
                        parts.append(f"类型: {n['node_type']}")
                    if len(segments) > 1:
                        parts.append(f"[{seg_idx + 1}/{len(segments)}]")
                    if raw_text:
                        parts.append(f"内容: {seg_text}")
                    else:
                        parts.append(f"功能路径: {func_path}")
                    text = "\n".join(parts)
                    chunk_id = f"figma_node:{n['node_id']}"
                    if len(segments) > 1:
                        chunk_id += f":{seg_idx}"
                    chunks.append(
                        {
                            "type": "figma_node",
                            "chunk_id": chunk_id,
                            "text": text,
                            "meta": base_meta,
                            "keywords": self._extract_keywords(text),
                        }
                    )

            # 3. figma_components（通常较短，保留单 chunk）
            comps = conn.execute(
                "SELECT component_id, name, description, variant_options "
                "FROM figma_components WHERE project=%s AND version_label=%s",
                (project, version_label),
            ).fetchall()
            for c in comps or []:
                text = c.get("description", "") or f"组件: {c.get('name', '')}"
                meta = {
                    "name": c.get("name", ""),
                    "component_id": c.get("component_id", ""),
                }
                vo = c.get("variant_options")
                if vo:
                    try:
                        meta["variant_options"] = (
                            json.loads(vo) if isinstance(vo, str) else vo
                        )
                    except Exception:
                        _log.debug(
                            "[RagEngine] figma component variant_options 解析失败: %s",
                            str(vo)[:80],
                            exc_info=True,
                        )
                chunks.append(
                    {
                        "type": "figma_component",
                        "chunk_id": f"figma_component:{c['component_id']}",
                        "text": text,
                        "meta": json.dumps(meta, ensure_ascii=False),
                        "keywords": self._extract_keywords(text),
                    }
                )
        finally:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("figma + figma + conn.close: %s", _e, exc_info=True)
    except Exception as e:
        _log.warning("[RagEngine] Figma 索引构建失败: %s", str(e)[:120])

    return chunks
