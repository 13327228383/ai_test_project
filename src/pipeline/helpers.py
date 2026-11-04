# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
流水线辅助模块 (pipeline_helpers)
=================================
从 pipeline_scheduler.py 拆分而来的独立辅助函数。

包含：
1. analyze_requirement_changes — AI 驱动的需求变更分析
2. build_db_context — 从数据库提取样例数据构建 AI 上下文
"""

import re
import logging

_log = logging.getLogger(__name__)

from src.core.pipeline_config import pipeline_config

# 表名合法字符：仅允许字母、数字、下划线
_TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def analyze_requirement_changes(
    old_text: str, new_text: str
) -> list[str] | None:
    """
    用 AI 对比新旧需求，提取 UI 相关的变更页面 ID。

    参数:
        old_text: 旧需求全文
        new_text: 新需求全文

    返回:
        None 表示应回退到全量拉取
        []   表示无 UI 变更
        [...] Figma 页面 ID 列表
    """
    if not old_text or not new_text:
        return None
    try:
        from src.ai.ai_client import ai_brain
        from src.core.config_loader import config

        system_prompt = (
            "你是需求分析专家。对比新旧需求文档，找出 UI/设计相关的变更。\n\n"
            "## 规则\n"
            "1. 只关注**会改变页面设计**的需求变更，忽略纯逻辑/后端变更\n"
            "   · UI 变更: 新增页面、修改布局、增删字段/按钮/表格列、改交互流程\n"
            "   · 非 UI 变更: 加日志、权限调整、性能优化、数据校验规则、\n"
            "     后端算法调整、定时任务、接口改动（前端页面不变）\n"
            "2. 对每个 UI 变更给出你的变更描述归纳\n"
            "3. 输出前自查：只保留 UI 相关变更\n\n"
            "## 输出格式（严格 JSON，不要其他文字）\n"
            '{"ui_changes": [\n'
            '  {"module": "功能模块名", "change_desc": "变更描述",\n'
            '   "figma_page_ids": ["131:14015"]}, ...],\n'
            ' "has_ui_changes": true/false,\n'
            ' "reasoning": "简要分析理由"}\n'
        )

        user_msg = (
            "## 旧需求\n```\n%s\n```\n\n"
            "## 新需求\n```\n%s\n```\n\n"
        ) % (old_text[:6000], new_text[:6000])

        result = ai_brain._call_api(
            system_prompt=system_prompt,
            user_message=user_msg,
            temperature=0.1,
            max_tokens=1024,
            caller_name="需求变更分析",
            task_type="requirement_analysis",
            force_cloud=True,
            seed=pipeline_config.ai_stability_seed,
        )
        if not result.get("success"):
            _log.info("  [需求分析] AI 调用失败，回退全量拉取")
            return None

        parsed = ai_brain._parse_json_response(result["data"])
        if not parsed.get("success"):
            return None

        data = parsed["data"]
        if not data.get("has_ui_changes"):
            _log.info(
                "  [需求分析] AI 判断: 无 UI 变更 (%s)", data.get("reasoning", "")[:80]
            )
            return []  # 空列表表示无变更，跳过 Figma

        all_ids = []
        for change in data.get("ui_changes", []):
            ids = change.get("figma_page_ids", [])
            all_ids.extend(ids)
            _log.info(
                "  [需求分析] %s → %s → %s"
                % (change.get("module", "?"), change.get("change_desc", "?")[:40], ids)
            )

        if all_ids:
            _log.info("  [需求分析] 共 %d 个 Figma 页面需要更新", len(all_ids))
            return all_ids
        return []
    except Exception as e:
        _log.warning("  [需求分析] 异常，回退全量拉取: %s", str(e)[:80], exc_info=True)
        return None


def build_db_context(max_tables: int = 10) -> str:
    """
    从数据库提取样例数据，为 AI 生成测试用例提供上下文。

    参数:
        max_tables: 最多读取的表数量，默认 10，避免慢查询。

    返回:
        数据库上下文字符串，可直接注入 AI Prompt
    """
    lines = []
    conn_was_none = False
    try:
        from src.extensions.db.db_agent import db_agent
        from src.core.config_loader import config

        if not config.db_enabled:
            return ""

        # 仅当未连接时才建立连接，避免重复 "[DatabaseAgent] 已连接" 日志
        if db_agent._conn is None:
            db_agent.connect()
            conn_was_none = True
        db_type = config.db_type.lower()

        # 获取表列表
        if db_type in ("yasdb", "oracle"):
            result = db_agent.query("SELECT TABLE_NAME FROM USER_TABLES")
        elif db_type == "mysql":
            # ★ 安全：使用参数化查询，而非字符串格式化
            db_name = config.db_name
            result = db_agent.query(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s LIMIT %s",
                (db_name, max_tables),
            )
        elif db_type == "postgresql":
            result = db_agent.query(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' LIMIT %s",
                (max_tables,),
            )
        elif db_type == "sqlite":
            result = db_agent.query(
                "SELECT name FROM sqlite_master WHERE type='table' LIMIT %s",
                (max_tables,),
            )
        else:
            result = db_agent.query("SELECT TABLE_NAME FROM USER_TABLES")

        if not result.get("success") or not result.get("data"):
            return ""

        tables = result["data"]
        # 确定标识符引号风格 + LIMIT 语法
        if db_type == "mysql":
            quote = "`"
        elif db_type == "sqlite":
            quote = ""
        else:
            quote = '"'
        limit_clause = (
            "FETCH FIRST 3 ROWS ONLY" if db_type in ("yasdb", "oracle") else "LIMIT 3"
        )

        for row in tables[:max_tables]:
            tname = list(row.values())[0] if isinstance(row, dict) else row
            if isinstance(tname, (list, tuple)):
                tname = tname[0]
            tname = str(tname).replace(quote, "")

            # ★ P0 安全校验：表名仅允许字母、数字、下划线，防止 SQL 注入
            if not _TABLE_NAME_RE.match(tname):
                _log.warning("  [DB上下文] 跳过非法表名: %r", tname)
                continue

            try:
                rows_result = db_agent.query(
                    "SELECT * FROM %s%s%s %s" % (quote, tname, quote, limit_clause)
                )
                if rows_result.get("success") and rows_result.get("data"):
                    lines.append("表 %s 样例数据:" % tname)
                    for r in rows_result["data"]:
                        lines.append("  %s" % str(r)[:200])
            except Exception as e:
                _log.debug("  [DB上下文] 查询表 %s 失败: %s", tname, str(e)[:60])
        lines.append("\n请基于以上表结构设计测试用例中的数据库证据计划。")

    except Exception as e:
        _log.warning("  [DB上下文] 数据库查询失败: %s", str(e)[:80])
        lines.append("\n[WARN] 数据库查询失败: %s\n" % e)
        # ★ P1: 异常时关闭连接，避免泄漏
        if conn_was_none:
            try:
                from src.extensions.db.db_agent import db_agent

                if db_agent._conn is not None:
                    db_agent.close()
                    _log.debug("  [DB上下文] 异常后已关闭连接")
            except Exception:
                _log.debug("  [DB上下文] 关闭连接时出错，忽略")
    return "\n".join(lines)


# ═══════════════════════════════════════════
# ★ v6: 追溯链路 + RAG索引 + 变更对比
# ═══════════════════════════════════════════


def build_rag_index(
    yapi_data: dict = None,
    db_schema: str = "",
    dom_snapshots: list[dict] = None,
    prototype_images: list[dict] = None,
    requirements: list[dict] = None,
    project: str = "",
    version_label: str = "",
) -> int:
    """
    Step1 完成后、Step2 开始前调用：构建 RAG 索引。

    从 build_traceability_and_diff 中分离出来，确保 Step2 拆解时已有 RAG 数据可用。
    project/version_label 由调用方（Step1 已解析出的上下文）显式传入，索引按
    「项目 + 版本」分区读写。

    返回: chunk 总数
    """
    try:
        from src.extensions.rag_engine import rag_engine

        _log.info("  [RAG] 开始构建 RAG 索引...")
        chunk_count = rag_engine.build_index(
            requirements=requirements,
            yapi_specs=yapi_data,
            db_schema=db_schema,
            dom_snapshots=dom_snapshots,
            prototype_images=prototype_images,
            project=project,
            version_label=version_label,
        )
        _log.info("  [RAG] RAG 索引构建完成: %d chunks", chunk_count)
        return chunk_count
    except Exception as e:
        _log.warning("  [WARN] RAG 索引构建失败: %s", str(e)[:80])
        return 0


def load_canonical_requirement_text(project: str, version_label: str = "") -> str:
    """读取 Step1 写入的 canonical 需求正文（doc_type='confluence'）。

    单一事实源：Step1 的需求-SUT 匹配与 Step2 的需求抽取都必须读这一行。
    该行正文已由 Step1 合并了全部 category='requirement' 的上传文档，若下游
    再把 upload_* 行叠加进来，同一份正文会被计数两次（需求模块数虚高）。

    返回已剥离合成头的正文；无记录返回空串。
    """
    conn = None
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()

        def _fetch(sql, params):
            cur = conn.execute(sql, params)
            return cur.fetchone() if cur else None

        row = None
        if version_label:
            row = _fetch(
                "SELECT content FROM source_documents "
                "WHERE project = %s AND doc_type = 'confluence' AND version_label = %s "
                "ORDER BY updated_at DESC LIMIT 1",
                (project, version_label),
            )
        if not row:
            row = _fetch(
                "SELECT content FROM source_documents "
                "WHERE project = %s AND doc_type = 'confluence' "
                "ORDER BY updated_at DESC LIMIT 1",
                (project,),
            )
        content = (row or {}).get("content") or ""
        if not content:
            return ""
        # 合成头以 "\n---\n" 结束（persist.py 写入），其前是标题/来源元信息
        body_start = content.find("\n---\n")
        return content[body_start + 5 :].strip() if body_start > 0 else content.strip()
    except Exception as _e:
        _log.warning("  [需求文档] 读取 canonical 需求正文失败: %s", str(_e)[:120])
        return ""
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _pass_e:
                _log.debug("连接关闭异常: %s", _pass_e)


def load_step2_dual_source_context() -> tuple:
    """Step2 后追溯链路的上下文加载 —— 双源读取的唯一实现。

    diff_snapshots 优先 → source_documents 兜底（MySQL-only，唯一事实源）。
    scheduler / streaming_scheduler 共用本函数，禁止再各自重抄。
    返回 (yapi_data, db_schema, dom_snapshots)。
    """
    import json as _json

    # ── yapi_data: diff_snapshots 优先 → source_documents 兜底 ──
    yapi_data = None
    try:
        from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

        yapi_data = diff_snapshot_store.load_snapshot("api_schema")
    except Exception as _e:
        _log.debug("加载 api_schema 快照异常: %s", str(_e)[:80])
        yapi_data = None
    _conn = None
    if not yapi_data:
        try:
            from src.core.config_loader import config

            _project_name = getattr(config, "project_name", "")
            from src.core.db_manager import get_connection

            _conn = get_connection()
            # 优先读 yapi_structured（JSON 结构化，含 interfaces 列表）
            _cur = _conn.execute(
                "SELECT content FROM source_documents "
                "WHERE project=%s AND doc_type='yapi_structured' ORDER BY updated_at DESC LIMIT 1",
                (_project_name,),
            )
            _row = _cur.fetchone() if _cur else None
            if _row and _row.get("content"):
                try:
                    yapi_data = _json.loads(_row["content"])
                except Exception as _je:
                    _log.debug("yapi_structured JSON解析失败: %s", str(_je)[:60])
                    yapi_data = None
                if yapi_data:
                    _log.info(
                        "api_schema: 从 source_documents(yapi_structured) 恢复 (%d 接口)",
                        (
                            len(yapi_data.get("interfaces", []))
                            if isinstance(yapi_data, dict)
                            else 0
                        ),
                    )
            # 退化：yapi_structured 不存在时读原始文本
            if not yapi_data:
                _cur2 = _conn.execute(
                    "SELECT content FROM source_documents "
                    "WHERE project=%s AND doc_type='yapi' ORDER BY updated_at DESC LIMIT 1",
                    (_project_name,),
                )
                _row2 = _cur2.fetchone() if _cur2 else None
                if _row2 and _row2.get("content"):
                    raw_content2 = _row2["content"]
                    try:
                        yapi_data = _json.loads(raw_content2)
                    except Exception:
                        yapi_data = {"raw_text": raw_content2}
                    _log.info(
                        "api_schema: 从 source_documents(yapi 文本) 恢复 (%d 字符)",
                        len(raw_content2),
                    )
        except Exception as _e:
            _log.debug("[traceability] source_documents yapi 回退失败: %s", str(_e)[:80])
        finally:
            if _conn is not None:
                try:
                    _conn.close()
                except Exception as _close_e:
                    _log.debug("_conn.close 失败: %s", str(_close_e)[:80])
    if not yapi_data:
        _log.info("api_schema 快照为空（尚未执行 Step1 YApi 拉取）")

    # ── db_schema: diff_snapshots 优先 → source_documents 兜底 ──
    db_schema = ""
    try:
        from src.pipeline.utils.diff_snapshot_store import (
            diff_snapshot_store as _ds_store,
        )

        db_snap = _ds_store.load_snapshot("db_schema")
        if isinstance(db_snap, dict) and "schema_text" in db_snap:
            db_schema = db_snap.get("schema_text", "")
    except Exception as _e:
        _log.debug("加载 db_schema 快照异常: %s", str(_e)[:80])
        db_schema = ""
    _conn2 = None
    if not db_schema:
        try:
            from src.core.config_loader import config

            _project_name2 = getattr(config, "project_name", "")
            from src.core.db_manager import get_connection

            _conn2 = get_connection()
            _cur3 = _conn2.execute(
                "SELECT content FROM source_documents "
                "WHERE project=%s AND doc_type IN ('database', 'db_schema') ORDER BY updated_at DESC LIMIT 1",
                (_project_name2,),
            )
            _row3 = _cur3.fetchone() if _cur3 else None
            if _row3 and _row3.get("content"):
                db_schema = _row3["content"]
                _log.info("db_schema: 从 source_documents 恢复 (%d 字符)", len(db_schema))
        except Exception as _e:
            _log.debug("[traceability] source_documents db_schema 回退失败: %s", str(_e)[:80])
        finally:
            if _conn2 is not None:
                try:
                    _conn2.close()
                except Exception as _close_e:
                    _log.debug("_conn2.close 失败: %s", str(_close_e)[:80])
    if not db_schema:
        _log.info("db_schema 快照为空（如需迁移旧缓存，请显式导入）")

    # ── DOM 快照（P0-1 读侧迁移: page_dom_snapshots → nav_graph） ──
    dom_snapshots = []
    with guard("load_step2_dual_source_context: from src.core.config_loader import c", log=_log, level=logging.ERROR):
        from src.core.config_loader import config

        _project_name3 = getattr(config, "project_name", "")
        from src.pipeline.utils.nav_graph_read import read_dom_pages

        dom_snapshots = read_dom_pages(_project_name3)
        _log.info("DOM 快照: 从 nav_graph 加载 %d 条", len(dom_snapshots))

    return yapi_data, db_schema, dom_snapshots


def build_traceability_and_diff(
    requirements: list[dict],
    yapi_data: dict = None,
    db_schema: str = "",
    dom_snapshots: list[dict] = None,
) -> dict:
    """
    Step2 完成后调用：构建追溯链路 + 变更快照对比。

    注意: RAG 索引已提前在 Step1→Step2 之间由 build_rag_index() 构建，
    本函数不再重复构建 RAG。

    返回: {"trace_links": ..., "diff_reports": ..., "rag_chunks": 0}
    """
    result = {"trace_links": {}, "diff_reports": {}, "rag_chunks": 0}

    # 1. 追溯链路（依赖 rag_chunks 已填充，否则 _chunk_exists 全空导致 trace_link_chunks 0 条写入）
    with guard("build_traceability_and_diff: from src.extensions.traceability_mod", log=_log, level=logging.WARNING):
        from src.extensions.traceability_model import traceability_engine

        trace_links = traceability_engine.build_trace_links(
            requirements=requirements,
            yapi_specs=yapi_data,
            db_schema=db_schema,
        )
        result["trace_links"] = trace_links
        traceability_engine.save_trace_links(trace_links)
        _log.debug("  [v6] 追溯链路: %d 条已构建", len(trace_links))

    # 2. 变更对比快照（★ 全维度：需求 + API + DB + 测试用例）
    with guard("build_traceability_and_diff: from src.extensions.diff_engine impo", log=_log, level=logging.WARNING):
        from src.extensions.diff_engine import diff_engine, impact_analyzer
        from src.core.excel_handler import excel_handler

        # ── 2a. 需求快照 + diff ──
        _log.info("  [v6] 开始保存 requirements 快照...")
        req_snapshot = [
            {
                "req_id": r.get("req_id"),
                "module": r.get("module"),
                "description": r.get("description"),
            }
            for r in requirements
        ]
        diff_engine.save_snapshot(req_snapshot, "requirements")
        _log.info("  [v6] requirements 快照保存完成，加载历史快照...")

        old_reqs = _load_previous_snapshot(diff_engine, "requirements")
        if old_reqs:
            old_ids = {r.get("req_id", "") for r in old_reqs}
            new_ids = {r.get("req_id", "") for r in requirements}
            if old_ids != new_ids:
                req_diff = diff_engine.diff_requirements(requirements, old_reqs)
                if req_diff.has_changes:
                    impact = impact_analyzer.analyze(
                        req_diff, result["trace_links"], []
                    )
                    result["diff_reports"]["requirements"] = {
                        "diff": req_diff,
                        "impact": impact,
                    }
                    _log.info(
                        "  [v6] 需求变更: +%d -%d ~%d, 影响 %d 条需求"
                        % (
                            req_diff.added_count,
                            req_diff.removed_count,
                            req_diff.modified_count,
                            len(impact.affected_requirements),
                        )
                    )

        # ── 2b. API 快照 + diff ──
        if yapi_data:
            _log.info("  [v6] 开始保存 api_schema 快照...")
            diff_engine.save_snapshot(yapi_data, "api_schema")
            _log.info("  [v6] api_schema 快照保存完成")
            old_yapi = _load_previous_snapshot(diff_engine, "api_schema")
            if old_yapi:
                api_diff = diff_engine.diff_api_schema(yapi_data, old_yapi)
                if api_diff.has_changes:
                    result["diff_reports"]["api_schema"] = {"diff": api_diff}
                    _log.info(
                        "  [v6] API变更: +%d -%d ~%d"
                        % (
                            api_diff.added_count,
                            api_diff.removed_count,
                            api_diff.modified_count,
                        )
                    )

        # ── 2c. DB schema 快照 + diff ──
        if db_schema:
            _log.info("  [v6] 开始保存 db_schema 快照...")
            diff_engine.save_snapshot({"schema_text": db_schema}, "db_schema")
            _log.info("  [v6] db_schema 快照保存完成")
            old_db = _load_previous_snapshot(diff_engine, "db_schema")
            if old_db:
                old_db_text = (
                    old_db.get("schema_text", "") if isinstance(old_db, dict) else ""
                )
                db_diff = diff_engine.diff_db_schema(db_schema, old_db_text)
                if db_diff.has_changes:
                    result["diff_reports"]["db_schema"] = {"diff": db_diff}
                    _log.info(
                        "  [v6] DB变更: +%d -%d ~%d"
                        % (
                            db_diff.added_count,
                            db_diff.removed_count,
                            db_diff.modified_count,
                        )
                    )

        # ── 2d. 测试用例快照（用于跨版本质量对比）──
        try:
            _log.info("  [v6] 开始读取测试用例...")
            intents = excel_handler.read_test_cases()
            _log.info("  [v6] 测试用例读取完成: %d 条", len(intents))
            if intents:
                intent_snapshot = [
                    {
                        "case_id": c.get("case_id"),
                        "title": c.get("title"),
                        "case_type": c.get("case_type"),
                        "risk_level": c.get("risk_level"),
                        "feature": c.get("feature", ""),
                        "verification_status": c.get("verification_status", ""),
                    }
                    for c in intents
                    if c.get("case_id")
                ]
                _log.info("  [v6] 开始保存 test_cases 快照...")
                diff_engine.save_snapshot(intent_snapshot, "test_cases")
                _log.info("  [v6] test_cases 快照保存完成")
                old_intents = _load_previous_snapshot(diff_engine, "test_cases")
                if old_intents:
                    case_diff = _diff_intent_snapshots(old_intents, intent_snapshot)
                    if case_diff["has_changes"]:
                        result["diff_reports"]["test_cases"] = {"diff": case_diff}
                        _log.info(
                            "  [v6] 测试用例变更: +%d -%d ~%d"
                            % (
                                case_diff["added"],
                                case_diff["removed"],
                                case_diff["modified"],
                            )
                        )
        except Exception as e:
            _log.warning("  [WARN] 测试用例快照跳过: %s", str(e)[:60])

        # ── 2e. 跨模块依赖关系快照（★ P0修复：Step3 读取此快照生成关联意图）──
        try:
            _log.info("  [v6] 开始构建 cross_dependencies 快照...")
            cross_deps = _extract_cross_dependencies(
                requirements, result.get("trace_links", {})
            )
            if cross_deps:
                diff_engine.save_snapshot(cross_deps, "cross_dependencies")
                _log.info(
                    "  [v6] cross_dependencies 快照保存完成: %d 个依赖对",
                    len(cross_deps),
                )
            else:
                _log.info("  [v6] cross_dependencies: 无跨模块依赖关系")
        except Exception as e:
            _log.warning("  [WARN] cross_dependencies 快照保存失败: %s", str(e)[:80])

        # 清理旧版本
        _log.info("  [v6] 开始清理旧版本快照...")
        diff_engine.cleanup_old_versions(keep_count=5)
        _log.info("  [v6] 旧版本快照清理完成")


    # ★ RAG 索引已移到追溯链路之前（见上方 step 0），此处不再重复构建

    return result


# ═══════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════


def _load_previous_snapshot(diff_engine, snapshot_type: str) -> dict | None:
    """加载上一个版本的快照（事实源：MySQL diff_snapshots）。"""
    try:
        from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

        versions = diff_snapshot_store.list_versions()
        # 跳过当前版本（= list_versions[0]），取第二个
        if len(versions) < 2:
            return None
        prev_ver = versions[1]
        return diff_snapshot_store.load_snapshot(snapshot_type, version_label=prev_ver)
    except Exception as e:
        _log.debug("  [快照加载] 加载上一版本快照失败: %s", str(e)[:60])
        return None


def _diff_intent_snapshots(old_intents: list[dict], new_intents: list[dict]) -> dict:
    """
    对比两个版本的测试用例快照（轻量级：基于 case_id 对比）。
    返回: {"has_changes": bool, "added": int, "removed": int, "modified": int, "added_ids": [...], ...}
    """
    old_map = {c.get("case_id", ""): c for c in old_intents if c.get("case_id")}
    new_map = {c.get("case_id", ""): c for c in new_intents if c.get("case_id")}

    old_ids = set(old_map.keys())
    new_ids = set(new_map.keys())

    added = list(new_ids - old_ids)
    removed = list(old_ids - new_ids)

    modified = []
    for cid in old_ids & new_ids:
        old_c = old_map[cid]
        new_c = new_map[cid]
        # 比较关键字段
        changed = False
        for field in ["title", "case_type", "risk_level", "verification_status"]:
            if str(old_c.get(field, "")) != str(new_c.get(field, "")):
                changed = True
                break
        if changed:
            modified.append(cid)

    return {
        "has_changes": bool(added or removed or modified),
        "added": len(added),
        "removed": len(removed),
        "modified": len(modified),
        "added_ids": added[:20],
        "removed_ids": removed[:20],
        "modified_ids": modified[:20],
        "total_old": len(old_intents),
        "total_new": len(new_intents),
    }


def _extract_cross_dependencies(
    requirements: list[dict], trace_links: dict
) -> list[dict]:
    """★ P0修复：从需求和追溯链路中提取跨模块依赖关系。
    返回 [{"from": req_id, "to": req_id, "reason": "..."}] 列表。"""
    deps = []
    if not requirements:
        return deps

    req_map = {r.get("req_id", ""): r for r in requirements}

    # 策略1：基于 module 字段——同模块的需求之间可能有依赖
    module_groups = {}
    for req in requirements:
        module = req.get("module", "") or req.get("feature", "") or "未分类"
        module_groups.setdefault(module, []).append(req)

    # 策略2：基于 trace_links——如果两个需求共享 API 或 DB 表，说明有依赖
    req_apis = {}  # req_id -> set of api paths
    req_tables = {}  # req_id -> set of table names
    for req_id, link in trace_links.items():
        if req_id not in req_map:
            continue
        apis = set()
        tables = set()
        if hasattr(link, "api_endpoints") and link.api_endpoints:
            for e in link.api_endpoints:
                path = e.get("path", "") if isinstance(e, dict) else str(e)
                if path:
                    apis.add(path)
        elif isinstance(link, dict):
            for e in link.get("api_endpoints", []):
                path = e.get("path", "") if isinstance(e, dict) else str(e)
                if path:
                    apis.add(path)
        if hasattr(link, "db_tables") and link.db_tables:
            if isinstance(link.db_tables, dict):
                tables = set(link.db_tables.keys())
            elif isinstance(link.db_tables, list):
                tables = set(str(t) for t in link.db_tables)
        elif isinstance(link, dict):
            db = link.get("db_tables", {})
            if isinstance(db, dict):
                tables = set(db.keys())
            elif isinstance(db, list):
                tables = set(str(t) for t in db)
        if apis:
            req_apis[req_id] = apis
        if tables:
            req_tables[req_id] = tables

    # 找共享 API/Table 的需求对
    req_ids = list(req_apis.keys())
    for i, a in enumerate(req_ids):
        for j in range(i + 1, len(req_ids)):
            b = req_ids[j]
            shared_apis = req_apis.get(a, set()) & req_apis.get(b, set())
            shared_tables = req_tables.get(a, set()) & req_tables.get(b, set())
            if shared_apis:
                deps.append(
                    {
                        "from": a,
                        "to": b,
                        "reason": "共享API: %s" % ", ".join(list(shared_apis)[:3]),
                    }
                )
            elif shared_tables:
                deps.append(
                    {
                        "from": a,
                        "to": b,
                        "reason": "共享数据表: %s" % ", ".join(list(shared_tables)[:3]),
                    }
                )

    # 去重（同方向只保留一条）
    seen = set()
    unique_deps = []
    for d in deps:
        key = (d["from"], d["to"])
        if key not in seen:
            seen.add(key)
            unique_deps.append(d)

    return unique_deps[:50]  # 限制数量
