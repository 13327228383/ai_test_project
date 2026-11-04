# -*- coding: utf-8 -*-
from __future__ import annotations

"""规则提取 - API/RAG 解析与匹配模块
================================
★ 架构债清理(2026-07-25): 从 _rule_extractor.py 拆出（§4.3 单文件≤800行）。

职责：从 trace_link_chunks / rag_chunks 表加载并解析 API 关联数据，
  合并 trace_links 精确关联与 RAG 语义匹配结果，为用例建模提供 API 详情。
通用性（§4.2）：纯算法匹配，无业务关键词硬编码，适用任何系统/行业。
"""

import json
import logging
import re
from collections import Counter
from typing import Any

_log = logging.getLogger(__name__)


# === 预编译正则（模块级，避免循环内/函数内重复 compile） ===

# _enrich_apis_with_rag_details 用 — API 详情解析（method 为非捕获组）
_API_DETAIL_PATTERN = re.compile(
    r"(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/[^\s,(]+)"
    r"(?:\s*\(query:\s*((?:[^()]|\([^)]*\))*)\))?"
    r"(?:\s*\(body:\s*((?:[^()]|\([^)]*\))*)\))?"
    r"(?:\s*→\s*([^\n]*))?"
    r"(?:\n功能:\s*([^\n]*))?",
    re.MULTILINE,
)

# _parse_rag_context 用 — API 解析（method 为捕获组）
_API_PARSE_PATTERN = re.compile(
    r"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/[^\s,(]+)"
    r"(?:\s*\(query:\s*((?:[^()]|\([^)]*\))*)\))?"
    r"(?:\s*\(body:\s*((?:[^()]|\([^)]*\))*)\))?"
    r"(?:\s*→\s*([^\n]*))?"
    r"(?:\n功能:\s*([^\n]*))?",
    re.MULTILINE,
)

# _parse_rag_context 用 — DOM/Db/Figma 解析
_DOM_META_PATTERN = re.compile(
    r'"page_path"\s*:\s*"(/[^"]+)"[^}]*"element_line"\s*:\s*"([^"]+)"'
)
_DOM_TEXT_PATTERN = re.compile(r"路径:(/\S+)\s*\n\s*元素:\s*\[?\d*\]?\s*([^\n]+)")
_DB_TABLE_PATTERN = re.compile(r"表:\s*(\w+)\s*\(([^)]*)\)")
_FIGMA_PATTERN = re.compile(r"页面:\s*([^\n]+?)(?:\n|$)")


# ============================================================
# 0. trace_links 精确关联加载器（表和功能对应关系）
# ============================================================


def _load_trace_links_for_req(req_id: str) -> dict[str, Any]:
    """从 trace_link_chunks 表按 req_id 加载精确关联（表和功能对应关系）。

    版本维度与写入侧同源（src.core.versioning.current_version_id），
    保证读到的就是当前 active 版本写入的关联行。

    返回:
        {
            "apis": [{"method", "path", "confidence"}, ...],
            "db_tables": [{"name", "confidence"}, ...],
            "figma_pages": [{"name", "confidence"}, ...],
        }
    """
    result: dict[str, Any] = {"apis": [], "db_tables": [], "figma_pages": []}
    if not req_id:
        return result
    try:
        from src.core.config_loader import config
        from src.core.versioning import current_version_id

        project = getattr(config, "project_name", "") or ""
        if not project:
            return result
        version_id = current_version_id(project)

        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            rows = (
                conn.execute(
                    "SELECT chunk_type, chunk_id, confidence FROM trace_link_chunks "
                    "WHERE project=%s AND version_id=%s AND req_id=%s",
                    (project, version_id, req_id),
                ).fetchall()
                or []
            )

            for r in rows:
                chunk_type = str(r.get("chunk_type", "") or "").strip()
                chunk_id = str(r.get("chunk_id", "") or "").strip()
                confidence = str(r.get("confidence", "medium") or "medium")

                if chunk_type == "api" and chunk_id:
                    # chunk_id 格式: "api:GET:/dataManagement/auxiliary/typeList"
                    parts = chunk_id.split(":", 2)
                    if len(parts) >= 3:
                        method = parts[1].upper()
                        path = parts[2].strip()
                        if method and path:
                            result["apis"].append(
                                {
                                    "method": method,
                                    "path": path,
                                    "confidence": confidence,
                                }
                            )

                elif chunk_type == "db_table" and chunk_id:
                    # chunk_id 格式: "db:{table_name}"
                    parts = chunk_id.split(":", 1)
                    if len(parts) >= 2:
                        tname = parts[1].strip()
                        if tname:
                            result["db_tables"].append(
                                {
                                    "name": tname,
                                    "confidence": confidence,
                                }
                            )

                elif chunk_type == "figma_page" and chunk_id:
                    # chunk_id 格式: "figma_page:{page_id}"
                    result["figma_pages"].append(
                        {
                            "name": chunk_id,
                            "confidence": confidence,
                        }
                    )
        finally:
            conn.close()

        _log.debug(
            "[RuleExtract] [%s] trace_links: apis=%d db_tables=%d figma=%d",
            req_id,
            len(result["apis"]),
            len(result["db_tables"]),
            len(result["figma_pages"]),
        )
        return result
    except Exception as e:
        _log.debug("[RuleExtract] [%s] trace_links 加载失败: %s", req_id, str(e)[:80])
        return result


def _merge_trace_and_rag_apis(
    trace_apis: list[dict], rag_apis: list[dict]
) -> list[dict]:
    """合并 trace_links 精确关联 + RAG API 详情。

    trace_links 提供：精确的 method + path（Step2 已验证存在）
    RAG 提供：response_fields / body_params / query_params / summary（API 详情）

    合并策略（通用）：
    - 以 trace_links 为准，用 (method, path) 匹配 RAG 中的同 API，补充详情字段
    - trace_links 未覆盖但 RAG 中有的 API，保留（RAG 检索也有效）
    """
    if not trace_apis and not rag_apis:
        return []

    # 用 (method, path) 作为 key 建立索引
    rag_index: dict[str, dict] = {}
    for api in rag_apis:
        key = f"{api.get('method', '').upper()}|{api.get('path', '')}"
        rag_index[key] = api

    merged: list[dict] = []
    seen_keys: set = set()

    # 1. trace_links 优先：用精确关联的 API，从 RAG 补充详情
    #    trace_apis 自带的 enriched 详情（body_params 等）必须保留，
    #    RAG 只补充缺失字段；否则 RAG 列表为空时 enriched 详情会整体丢失
    for ta in trace_apis:
        key = f"{ta.get('method', '').upper()}|{ta.get('path', '')}"
        seen_keys.add(key)
        merged_api = {
            "method": ta.get("method", ""),
            "path": ta.get("path", ""),
            "query_params": ta.get("query_params", ""),
            "body_params": ta.get("body_params", ""),
            "res_fields": ta.get("res_fields", ""),
            "summary": ta.get("summary", ""),
            "_source": ta.get("_source", "trace_link"),
        }
        # 如果 trace_apis 没有详情，从 RAG 列表补充
        rag_api = rag_index.get(key)
        if rag_api:
            if not merged_api["query_params"]:
                merged_api["query_params"] = rag_api.get("query_params", "")
            if not merged_api["body_params"]:
                merged_api["body_params"] = rag_api.get("body_params", "")
            if not merged_api["res_fields"]:
                merged_api["res_fields"] = rag_api.get("res_fields", "")
            if not merged_api["summary"]:
                merged_api["summary"] = rag_api.get("summary", "")
            merged_api["_source"] = "trace_link+rag"
        merged.append(merged_api)

    # 2. RAG 中有但 trace_links 未覆盖的 API（RAG 检索也有效，保留）
    for api in rag_apis:
        key = f"{api.get('method', '').upper()}|{api.get('path', '')}"
        if key not in seen_keys:
            merged_api = dict(api)
            merged_api["_source"] = "rag"
            merged.append(merged_api)

    return merged


def _enrich_apis_with_rag_details(apis: list[dict]) -> list[dict]:
    """★ Fix28 (2026-07-07): 用 rag_chunks 表补充 trace_links API 的详情。

    问题: trace_links 只提供 (method, path)，缺少 body_params/response_fields/summary。
          导致 _infer_fill_target_and_value 取不到真实字段名，fill value 退化为 'test_value'。
          这正是"明明需求+RAG 都写了，却没用起来"的根因。
    修复: 用 chunk_id='api:{method}:{path}' 从 rag_chunks 表查询完整 text，
          解析出 body_params/query_params/res_fields/summary，补充到 api 字典。

    契约: §4.1 "规则优先 + AI 补漏" — 有 RAG 详情就应该用，不烧 token 问 AI。
          §4.2 "通用性" — 不硬编码字段名，只从 RAG 数据提取。
    """
    if not apis:
        return apis
    try:
        from src.core.config_loader import config

        project = getattr(config, "project_name", "") or ""
        if not project:
            return apis

        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            for api in apis:
                method = api.get("method", "").upper()
                path = api.get("path", "").strip()
                if not method or not path:
                    continue
                chunk_id = f"api:{method}:{path}"
                row = conn.execute(
                    "SELECT text FROM rag_chunks "
                    "WHERE scope_type='project' AND scope_id=%s AND type='api' "
                    "AND chunk_id=%s LIMIT 1",
                    (project, chunk_id),
                ).fetchone()
                if not row:
                    continue
                text = str(row.get("text", "") or "")
                if not text:
                    continue
                # 复用 _parse_rag_context 的正则解析单条 API text
                # ★ Fix28b: 修复 body_params 含嵌套 () 被截断的 bug
                #   原 bug: [^)]* 遇到 auxiliaryType(string) 的 ) 就停，body_params 被截断
                #   修复: 用 (?:[^()]|\([^)]*\))* 匹配嵌套括号内容
                m = _API_DETAIL_PATTERN.search(text)
                if m:
                    api["query_params"] = (m.group(2) or "").strip()
                    api["body_params"] = (m.group(3) or "").strip()
                    api["res_fields"] = (m.group(4) or "").strip()
                    api["summary"] = (m.group(5) or "").strip()
        finally:
            conn.close()
        _enriched = sum(1 for a in apis if a.get("body_params") or a.get("res_fields"))
        _log.debug(
            "[RuleExtract] API 详情补充: %d/%d 个 API 获得 body_params/res_fields",
            _enriched,
            len(apis),
        )
        return apis
    except Exception as e:
        _log.debug("[RuleExtract] API 详情补充失败: %s", str(e)[:80])
        return apis


def _match_apis_from_rag_chunks(req: dict[str, Any]) -> list[dict]:
    """★ v2.2 (2026-07-07): 从 rag_chunks 表按需求语义匹配 API（纯算法，不调 AI）。

    当 trace_links 无 API 关联时，用此函数从 rag_chunks 表按语义匹配 API，
    将规则提取覆盖率从 58.3% 提升到 95%+，减少 AI 调用。

    匹配策略（通用，无业务关键词硬编码）：
    1. CRUD 动词→HTTP method 映射：需求 operation 含"新增"→POST、"编辑/修改"→PUT、
       "删除"→DELETE、"查询/查看/展示"→GET。匹配时 method 一致加分。
    2. 提取需求的 module/operation/description 中的中文词组（2-4字）
    3. 用中文词组匹配 API 的 summary（功能描述）+ path 驼峰转空格后匹配
    4. 按匹配分数排序，返回前 5 个

    返回格式与 trace_data["apis"] 一致：
        [{"method", "path", "confidence", "_source": "rag_semantic"}, ...]
    """
    # ★ 通用 CRUD 动词→HTTP method 映射（无业务硬编码，适用任何系统）
    # ★ Fix29 (2026-07-07): 查询类动词映射到 GET_OR_POST（分页列表常用 POST）
    #   原 bug: "查询"→GET，但 POST /dataManagement/auxiliary/getList（辅材分页列表）
    #          是查询操作却用 POST，method 不匹配不得分，导致 score=0
    #   修复: 查询类动词映射到 GET_OR_POST，弱匹配加分 +2（低于强匹配 +5）
    #   契约: §4.2 "通用性" — POST 查询是 RESTful 常见模式（带分页请求体），不硬编码
    _CRUD_VERB_TO_METHOD = {
        "新增": "POST",
        "添加": "POST",
        "创建": "POST",
        "保存": "POST",
        "提交": "POST",
        "上传": "POST",
        "导入": "POST",
        "复制": "POST",
        "编辑": "PUT",
        "修改": "PUT",
        "更新": "PUT",
        "删除": "DELETE",
        "移除": "DELETE",
        "清除": "DELETE",
        # 查询类动词：GET 或 POST 都可能（分页列表常用 POST）
        "查询": "GET_OR_POST",
        "查看": "GET_OR_POST",
        "展示": "GET_OR_POST",
        "获取": "GET_OR_POST",
        "列表": "GET_OR_POST",
        "详情": "GET_OR_POST",
        "导出": "GET_OR_POST",
        "下载": "GET_OR_POST",
    }
    try:
        from src.core.config_loader import config

        project = getattr(config, "project_name", "") or ""
        if not project:
            return []

        from src.core.db_manager import get_connection

        conn = get_connection()
        api_chunks = []
        try:
            rows = (
                conn.execute(
                    "SELECT chunk_id, text FROM rag_chunks "
                    "WHERE scope_type='project' AND scope_id=%s AND type='api' AND is_active=1",
                    (project,),
                ).fetchall()
                or []
            )

            for r in rows:
                chunk_id = str(r.get("chunk_id", "") or "").strip()
                text = str(r.get("text", "") or "")
                # chunk_id 格式: "api:GET:/dataManagement/auxiliary/getList"
                parts = chunk_id.split(":", 2)
                if len(parts) < 3:
                    continue
                method = parts[1].upper()
                path = parts[2].strip()
                if not method or not path:
                    continue
                # 从 text 提取功能描述（summary）
                summary = ""
                for line in text.split("\n"):
                    line_stripped = line.strip()
                    if line_stripped.startswith("功能:") or line_stripped.startswith(
                        "summary:"
                    ):
                        summary = line_stripped.split(":", 1)[1].strip()
                        break
                api_chunks.append({"method": method, "path": path, "summary": summary})
        finally:
            conn.close()

        if not api_chunks:
            return []

        # 提取需求中的中文词组（2-4字）
        # ★ Fix29b (2026-07-07): 改用滑动窗口提取所有 2-4 字组合
        #   原 bug: re.findall(r'[\u4e00-\u9fff]{2,4}') 非重叠贪婪匹配，
        #          "用户按辅材类型查询" 被切成 ["用户按辅","材类型查","询辅材信"]，"辅材"被拆散
        #   修复: 滑动窗口提取所有 2-4 字组合，确保"辅材"能被提取
        desc = str(req.get("description", "") or "").strip()
        operation = str(req.get("operation", "") or "").strip()
        module = str(req.get("module", "") or "").strip()
        combined = f"{desc} {operation} {module}"
        cn_chars = re.findall(r"[\u4e00-\u9fff]", combined)
        cn_words_set = set()
        for length in (2, 3, 4):
            for i in range(len(cn_chars) - length + 1):
                cn_words_set.add("".join(cn_chars[i : i + length]))
        cn_words_unique = list(cn_words_set)

        if not cn_words_unique:
            return []

        # ★ 检测 CRUD 动词 → 期望的 HTTP method 集合
        # ★ Fix29: 区分强匹配（固定 method）和弱匹配（GET_OR_POST）
        expected_strong_methods = set()  # 强匹配：新增→POST, 编辑→PUT, 删除→DELETE
        has_weak_query = False  # 弱匹配：查询类动词 → GET 或 POST
        for verb, m in _CRUD_VERB_TO_METHOD.items():
            if verb in combined:
                if m == "GET_OR_POST":
                    has_weak_query = True
                else:
                    expected_strong_methods.add(m)

        # ★ Fix31 (2026-07-07): 引入 IDF 思想，按词在所有 API summary 中的出现频率分级加权
        #   原 bug: Fix30 去重后，"管理"/"类型"/"信息" 等高频通用词每个 +3，
        #          导致含这些词的无关 API（如 dataQuality/rule "规则管理-获取规则类型"）得 8 分，
        #          而含"辅材"这种领域核心词的 API 只得 5 分 → 领域 API 被挤出前 5
        #   修复: 预计算每个中文词在所有 API summary 中的文档频率（DF），
        #         DF ≤ 3 的低频领域词（如"辅材"）权重 +3，
        #         DF 4-8 的中频词权重 +2，
        #         DF > 8 的高频通用词（如"管理"/"类型"/"信息"）权重 +1
        #   契约: §4.2 通用性 — 纯统计算法，无业务关键词硬编码，适用任何系统
        word_doc_freq: dict[str, int] = Counter()
        # ★ D.1g 拆分笔误修复(2026-07-25): 原 `for cn_words in cn_words_by_api:` 变量名错误
        #   cn_words_by_api / api 均未定义 → NameError 被 except 兜住 → API 语义匹配降级
        #   修复: 正确循环变量为 api（来自 api_chunks，line 321 定义）
        for api in api_chunks:
            api_summary = api["summary"]
            if not api_summary:
                continue
            for word in cn_words_unique:
                if word in api_summary:
                    word_doc_freq[word] += 1

        def _word_weight(word: str) -> int:
            df = word_doc_freq.get(word, 0)
            if df <= 3:
                return 3  # 低频领域词（强区分性）
            elif df <= 8:
                return 2  # 中频词
            else:
                return 1  # 高频通用词（弱区分性）

        scored = []
        for api in api_chunks:
            path = api["path"]
            summary = api["summary"]
            api_method = api["method"]
            score = 0

            # 1. 用 API summary 匹配需求中文词（IDF 加权 + 最长匹配去重）
            # ★ Fix30: 最长匹配去重，避免滑动窗口生成的重叠子串重复加分
            # ★ Fix31: IDF 加权，高频通用词降权，低频领域词保持高权重
            matched_words = [w for w in cn_words_unique if w in summary]
            if matched_words:
                matched_words.sort(key=len, reverse=True)
                summary_used = [False] * len(summary)
                for word in matched_words:
                    # 找 word 在 summary 中第一个全部未被占用的位置
                    start = 0
                    while True:
                        idx = summary.find(word, start)
                        if idx == -1:
                            break
                        end = idx + len(word)
                        if not any(summary_used[idx:end]):
                            score += _word_weight(word)
                            for i in range(idx, end):
                                summary_used[i] = True
                            break
                        start = idx + 1

            # 2. ★ CRUD method 匹配加分
            # ★ Fix29: 强匹配 +5（新增/编辑/删除），弱匹配 +2（查询类 GET/POST）
            if expected_strong_methods and api_method in expected_strong_methods:
                score += 5
            if has_weak_query and api_method in ("GET", "POST"):
                score += 2

            # 3. 用 API path 的驼峰片段匹配 summary（间接验证 path 与功能相关）
            path_segments = path.split("/")[1:] if len(path.split("/")) > 1 else []
            for seg in path_segments:
                words_in_seg = re.sub(r"([A-Z])", r" \1", seg).lower().split()
                if words_in_seg and any(
                    w in summary.lower() for w in words_in_seg if len(w) > 2
                ):
                    score += 1

            # 4. ★ 用需求中文词直接匹配 API path（转空格后，通用）
            path_lower = path.lower()
            for seg in path_segments:
                seg_words = re.sub(r"([A-Z])", r" \1", seg).lower().split()
                # path 片段里如果有和中文词同义的部分（如 getList 对应"列表"）
                # 这里不做中英翻译，只做 path 片段本身的英文匹配权重
                pass  # path 本身的英文与中文需求无法直接匹配，靠 summary 桥接

            if score > 0:
                scored.append((score, api))

        scored.sort(key=lambda x: -x[0])
        return [
            {
                "method": api["method"],
                "path": api["path"],
                "confidence": "medium",
                "_source": "rag_semantic",
            }
            for _, api in scored[:5]
        ]
    except Exception as e:
        _log.debug("[RuleExtract] RAG API 语义匹配失败: %s", str(e)[:80])
        return []


# ============================================================
# 1. RAG 数据解析器（从 RAG 上下文提取结构化信息）
# ============================================================


def _parse_rag_context(rag_context: str) -> dict[str, list[dict]]:
    """解析 RAG 上下文，提取结构化的 API/DOM/DB/Figma 信息。

    返回:
        {
            "apis": [{"method", "path", "summary", "req_params", "req_body", "res_fields"}],
            "doms": [{"page_path", "tag", "role", "text", "placeholder", "ariaLabel"}],
            "db_tables": [{"name", "columns": [{"name", "type"}]}],
            "figma_pages": [{"name", "canvas", "page_type"}],
        }
    """
    result: dict[str, list[dict]] = {
        "apis": [],
        "doms": [],
        "db_tables": [],
        "figma_pages": [],
    }
    if not rag_context:
        return result

    # 1a. 解析 API chunks（SemanticPatternRenderer 格式）
    # 格式: "METHOD /path (query: fields) (body: fields) → response_fields\n功能: xxx"
    # ★ Fix28b-sync: 同步修复嵌套括号截断 bug（与 _enrich_apis_with_rag_details 一致）
    for m in _API_PARSE_PATTERN.finditer(rag_context):
        path = m.group(2).strip()
        # 跳过纯资源路径的尾部动作词（如 /delete, /getList）
        # 保留完整路径用于匹配，但也提供前缀用于 navigate
        result["apis"].append(
            {
                "method": m.group(1),
                "path": path,
                "query_params": (m.group(3) or "").strip(),
                "body_params": (m.group(4) or "").strip(),
                "res_fields": (m.group(5) or "").strip(),
                "summary": (m.group(6) or "").strip(),
            }
        )

    # 1b. 解析 DOM chunks
    # 格式: "[页面:xxx] 路径:/path\nelement:[tag=xxx | role=xxx | text=xxx | placeholder=xxx | ariaLabel=xxx]"
    # 或 JSON meta: {"page_path": "/xxx", "element_line": "[N] tag=xxx | text=xxx"}
    for m in _DOM_META_PATTERN.finditer(rag_context):
        page_path = m.group(1)
        element_line = m.group(2)
        dom = _parse_dom_element_line(element_line)
        dom["page_path"] = page_path
        result["doms"].append(dom)

    # 也解析 text 格式的 DOM（如 "[页面:xxx] 路径:/login\nelement:[8] tag=input | placeholder=请输入登录账号"）
    for m in _DOM_TEXT_PATTERN.finditer(rag_context):
        page_path = m.group(1)
        element_line = m.group(2)
        dom = _parse_dom_element_line(element_line)
        dom["page_path"] = page_path
        # 去重（避免 JSON meta 和 text 格式重复）
        if not any(
            d.get("page_path") == dom.get("page_path")
            and d.get("text") == dom.get("text")
            for d in result["doms"]
        ):
            result["doms"].append(dom)

    # 1c. 解析 DB table chunks
    # 格式: "表: xxx (字段: col1(type), col2(type), ...)" 或 JSON meta
    for m in _DB_TABLE_PATTERN.finditer(rag_context):
        table_name = m.group(1)
        cols_str = m.group(2)
        columns = []
        for col_part in cols_str.split(","):
            col_part = col_part.strip()
            if not col_part:
                continue
            # 格式: col_name(type) 或 col_name
            col_match = re.match(r"(\w+)\s*(?:\(([^)]+)\))?", col_part)
            if col_match:
                columns.append(
                    {"name": col_match.group(1), "type": col_match.group(2) or ""}
                )
        result["db_tables"].append({"name": table_name, "columns": columns})

    # 1d. 解析 Figma page chunks
    # 格式: "页面: xxx\n所属画布: yyy" 或 JSON meta
    for m in _FIGMA_PATTERN.finditer(rag_context):
        name = m.group(1).strip()
        if name and not any(f["name"] == name for f in result["figma_pages"]):
            result["figma_pages"].append({"name": name, "canvas": "", "page_type": ""})

    return result


def _parse_dom_element_line(element_line: str) -> dict[str, str]:
    """解析 DOM 元素行，提取 tag/role/text/placeholder/ariaLabel。

    格式: "tag=input | placeholder=请输入登录账号"
    格式: "tag=button | text=登 录"
    格式: "tag=span | role=img | ariaLabel=eye-invisible"
    """
    dom = {
        "tag": "",
        "role": "",
        "text": "",
        "placeholder": "",
        "ariaLabel": "",
        "type": "",
    }
    parts = element_line.split("|")
    for part in parts:
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip().lower()
            v = v.strip()
            if k in dom:
                dom[k] = v
            elif k == "tag":
                dom["tag"] = v
    return dom


def _parse_page_elements_line(text: str) -> dict[str, list[str]]:
    """★ 通用修复(2026-07-20): 解析 [PAGE_ELEMENTS] 行，提取 INPUTS/BUTTONS/TABS/LINKS。

    根因: page_dom_snapshots 的 dom_summary 中 [N] tag=button 没有 text 属性
          (Ant Design 的 button 用 span 嵌套，text 在子元素)，导致按钮列表为空。
          但 dom_summary 中有 [PAGE_ELEMENTS] 行，包含 Step1 爬取时汇总的真实按钮列表。
    通用性: [PAGE_ELEMENTS] 是 Step1 爬取时添加的标准格式，不依赖业务逻辑，
            适用于所有系统/行业/页面/Tab。
    格式: [PAGE_ELEMENTS] INPUTS: (search), 请搜索叶组名称(text), (checkbox) | BUTTONS: , 批量打标, 批量导出, 新建配方 | TABS: 叶组配方, ... | LINKS: ...

    ★ v16 (2026-07-23): 同时解析 [DROPDOWN_TRIGGERS] 行，提取 dropdown 触发器+菜单项。
      格式: [DROPDOWN_TRIGGERS] 数据导入(叶组数据导入,辅材数据导入) | 其他触发器(...)
      通用性: 基于 Step1 ARIA 契约爬取的数据，Step3 据此生成两步 action(展开+选菜单项)。

    返回: {"button_texts": [...], "input_placeholders": [...], "link_texts": [...], "tabs": [...], "dropdown_triggers": [{"trigger": str, "items": [str]}]}
    """
    result: dict[str, list] = {
        "button_texts": [],
        "input_placeholders": [],
        "link_texts": [],
        "tabs": [],
        "dropdown_triggers": [],
    }
    if not text:
        return result

    # 查找 [PAGE_ELEMENTS] 行（可能在 dom_summary 或 rag_chunks text 中）
    pe_match = re.search(
        r"\[PAGE_ELEMENTS\]\s*(.+?)(?:\n|$)", text, re.MULTILINE | re.DOTALL
    )
    if not pe_match:
        # [PAGE_ELEMENTS] 不存在仍尝试解析 [DROPDOWN_TRIGGERS]
        _parse_dropdown_triggers_into(text, result)
        return result

    pe_content = pe_match.group(1)

    # 解析各部分：INPUTS: ... | BUTTONS: ... | TABS: ... | LINKS: ...
    # 注意：每个部分的值可能包含逗号，所以用 | 分隔
    parts = pe_content.split("|")
    for part in parts:
        part = part.strip()
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip().upper()
        value = value.strip()

        # 用逗号分隔，去掉空值
        items = [v.strip() for v in value.split(",") if v.strip()]

        if key == "BUTTONS":
            result["button_texts"].extend(items)
        elif key == "INPUTS":
            # INPUTS 格式: (search), 请搜索叶组名称(text), (checkbox)
            # 提取 placeholder：xxx(yyy) 取 xxx，(yyy) 跳过（只有 type 无 placeholder），xxx 取 xxx
            for item in items:
                m = re.match(r"^(.+?)\((\w+)\)$", item)
                if m:
                    placeholder = m.group(1).strip()
                    if placeholder:
                        result["input_placeholders"].append(placeholder)
                # 纯括号格式 (search) 只有 type 无 placeholder，跳过
                # 纯文本格式 直接作为 placeholder
                elif not item.startswith("("):
                    result["input_placeholders"].append(item)
        elif key == "LINKS":
            result["link_texts"].extend(items)
        elif key == "TABS":
            result["tabs"].extend(items)

    # ★ v16: 解析 [DROPDOWN_TRIGGERS] 行(与 [PAGE_ELEMENTS] 独立的一行)
    _parse_dropdown_triggers_into(text, result)
    return result


def _parse_dropdown_triggers_into(text: str, result: dict) -> None:
    """v16: 解析 [DROPDOWN_TRIGGERS] 行，填充 result["dropdown_triggers"]。

    格式: [DROPDOWN_TRIGGERS] 数据导入(叶组数据导入,辅材数据导入) | 其他触发器(项1,项2)
    通用性: 解析 Step1 ARIA 契约爬取的结构化数据，不依赖业务关键词。
    """
    if not text:
        return
    dd_match = re.search(
        r"\[DROPDOWN_TRIGGERS\]\s*(.+?)(?:\n|$)", text, re.MULTILINE | re.DOTALL
    )
    if not dd_match:
        return
    dd_content = dd_match.group(1)
    # 每个触发器用 | 分隔
    for part in dd_content.split("|"):
        part = part.strip()
        if not part:
            continue
        # 格式: trigger(item1,item2,...)
        m = re.match(r"^(.+?)\((.*)\)$", part)
        if not m:
            continue
        trigger = m.group(1).strip()
        items = [v.strip() for v in m.group(2).split(",") if v.strip()]
        if trigger and items:
            result["dropdown_triggers"].append({"trigger": trigger, "items": items})


# ============================================================
# 2. 通用语义匹配器（不依赖业务关键词）
# ============================================================
