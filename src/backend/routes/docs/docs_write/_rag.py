# -*- coding: utf-8 -*-
"""
文档中心 — RAG 索引重建路由
"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)

from flask import jsonify

from src.backend.routes.docs import docs_bp


@docs_bp.route("/api/docs/rag/rebuild", methods=["POST"])
def _api_rag_rebuild():
    """强制重建 RAG 索引（仅重建当前用户的 chunks，不影响其他用户）。"""
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin, is_project_admin

    uid = get_current_user()
    if not is_admin(uid) and not is_project_admin(uid):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "无权重建 RAG 索引（需要项目管理员或系统管理员权限）",
                }
            ),
            403,
        )
    try:
        from src.extensions.rag_engine import rag_engine
        from src.core.config_loader import config
        from src.core.excel_handler import excel_handler
        from src.core.session_context import session_ctx

        # ★ B3: 用户级隔离，仅清除当前用户的 chunks
        rag_engine.clear_index(user_id=uid)
        all_reqs = excel_handler.read_requirements()

        db_schema = ""
        with guard("_api_rag_rebuild: db_schema = session_ctx.for_traceabi", log=_log, level=logging.WARNING):
            db_schema = session_ctx.for_traceability()
        # ★ 回退：source_documents(db_schema / database)
        if not db_schema:
            with guard("_api_rag_rebuild: from src.core.config_loader import c", log=_log, level=logging.WARNING):
                from src.core.config_loader import config as _cfg_r
                from src.core.db_manager import get_connection as _get_conn_r
                from src.core.versioning import current_version_label as _cvl_r

                _vlabel_r = _cvl_r(_cfg_r.project_name) or ""
                _conn_d = _get_conn_r()
                try:
                    if _vlabel_r:
                        _row_d = _conn_d.execute(
                            "SELECT content FROM source_documents "
                            "WHERE project=%s AND version_label=%s AND doc_type IN ('database', 'db_schema') "
                            "ORDER BY updated_at DESC LIMIT 1",
                            (_cfg_r.project_name, _vlabel_r),
                        ).fetchone()
                    else:
                        _row_d = _conn_d.execute(
                            "SELECT content FROM source_documents "
                            "WHERE project=%s AND doc_type IN ('database', 'db_schema') "
                            "ORDER BY updated_at DESC LIMIT 1",
                            (_cfg_r.project_name,),
                        ).fetchone()
                    if _row_d and _row_d.get("content"):
                        db_schema = _row_d["content"]
                        _log.info(
                            "  [INFO] RAG：从 source_documents 恢复 DB schema (%d 字符)",
                            len(db_schema),
                        )
                finally:
                    try:
                        _conn_d.close()
                    except Exception as _pass_e:
                        _log.warning(
                            "_rag.py: _conn_d.close: %s", _pass_e, exc_info=True
                        )
        if not db_schema:
            _log.info(
                "  [INFO] RAG：DB schema 为空（_parse_db_from_live 将尝试直连目标 DB）"
            )

        # 加载 YApi（★ MySQL-only：diff_snapshots → source_documents 两级回退）
        yapi_data = None
        with guard("_api_rag_rebuild: from src.pipeline.utils.diff_snapsho", log=_log, level=logging.WARNING):
            from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

            yapi_data = diff_snapshot_store.load_snapshot("api_schema")
        if not yapi_data:
            # ★ 回退 1：source_documents(yapi_structured) — 结构化 JSON
            with guard("_api_rag_rebuild: from src.core.config_loader import c", log=_log, level=logging.WARNING):
                from src.core.config_loader import config
                from src.core.db_manager import get_connection as _get_conn
                from src.core.versioning import current_version_label as _cvl

                _vlabel = _cvl(config.project_name) or ""
                _conn_y = _get_conn()
                try:
                    if _vlabel:
                        _row_y = _conn_y.execute(
                            "SELECT content FROM source_documents "
                            "WHERE project=%s AND version_label=%s AND doc_type='yapi_structured' ORDER BY updated_at DESC LIMIT 1",
                            (config.project_name, _vlabel),
                        ).fetchone()
                    else:
                        _row_y = _conn_y.execute(
                            "SELECT content FROM source_documents "
                            "WHERE project=%s AND doc_type='yapi_structured' ORDER BY updated_at DESC LIMIT 1",
                            (config.project_name,),
                        ).fetchone()
                    if _row_y and _row_y.get("content"):
                        import json as _json

                        yapi_data = _json.loads(_row_y["content"])
                        _log.info(
                            "  [INFO] RAG：从 source_documents(yapi_structured) 恢复 (%d 接口)",
                            (
                                len(yapi_data.get("interfaces", []))
                                if isinstance(yapi_data, dict)
                                else 0
                            ),
                        )
                finally:
                    try:
                        _conn_y.close()
                    except Exception as _pass_e:
                        _log.warning(
                            "_rag.py: _conn_y.close: %s", _pass_e, exc_info=True
                        )
        if not yapi_data:
            # ★ 回退 2：source_documents(yapi) — 原始文本
            with guard("_api_rag_rebuild: from src.core.config_loader import c", log=_log, level=logging.WARNING):
                from src.core.config_loader import config
                from src.core.db_manager import get_connection as _get_conn
                from src.core.versioning import current_version_label as _cvl

                _vlabel = _cvl(config.project_name) or ""
                _conn_y2 = _get_conn()
                try:
                    if _vlabel:
                        _row_y2 = _conn_y2.execute(
                            "SELECT content FROM source_documents "
                            "WHERE project=%s AND version_label=%s AND doc_type='yapi' ORDER BY updated_at DESC LIMIT 1",
                            (config.project_name, _vlabel),
                        ).fetchone()
                    else:
                        _row_y2 = _conn_y2.execute(
                            "SELECT content FROM source_documents "
                            "WHERE project=%s AND doc_type='yapi' ORDER BY updated_at DESC LIMIT 1",
                            (config.project_name,),
                        ).fetchone()
                    if _row_y2 and _row_y2.get("content"):
                        import json as _json

                        raw_content = _row_y2["content"]
                        try:
                            yapi_data = _json.loads(raw_content)
                        except Exception:
                            yapi_data = {"raw_text": raw_content}
                        _log.info(
                            "  [INFO] RAG：从 source_documents(yapi 文本) 恢复 (%d 字符)",
                            len(raw_content),
                        )
                finally:
                    try:
                        _conn_y2.close()
                    except Exception as _pass_e:
                        _log.warning(
                            "_rag.py: _conn_y2.close: %s", _pass_e, exc_info=True
                        )
        if not yapi_data:
            _log.info("  [INFO] RAG：YApi 数据为空（需先执行 Step1 拉取）")

        chunk_count = rag_engine.build_index(
            requirements=all_reqs,
            yapi_specs=yapi_data,
            db_schema=db_schema,
            force_refresh=True,
            user_id=uid,
        )
        # ★ v4: 重建索引后清除 EntityResolver 全局缓存
        with guard("_api_rag_rebuild: from src.extensions.rag_engine.entit", log=_log, level=logging.WARNING):
            from src.extensions.rag_engine.entity_resolver import clear_global_cache

            clear_global_cache()
        return jsonify({"success": True, "chunk_count": chunk_count})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
