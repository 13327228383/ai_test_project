# -*- coding: utf-8 -*-
"""
Step1DataFetcher Confluence 拉取 mixin

职责：
- _fetch_confluence — Confluence 拉取（缓存检查、API 拉取、MySQL 写入）
- _merge_uploaded_docs — 上传文档合并
"""

import hashlib
from src.core.guarded import guard
import logging
import time

from src.core.config_loader import config

_log = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 7776000  # 90天
_TRUNC_DETAIL = 80
_TRUNC_STATUS = 100


class _Step1ConfluenceMixin:
    """Confluence 拉取能力。"""

    def _fetch_confluence(
        self,
        active_ver,
        _version_label,
        figma_force_refresh,
        force_overwrite=False,
        run_id=None,
    ):
        """拉取 Confluence，返回 (cf_ok, req_text, cf_result, cf_changed, cf_from_cache, old_req_text, image_urls)。

        ★ MySQL 唯一事实源：缓存判新鲜度/读一律查 source_documents(confluence)，不再有磁盘缓存概念。
        ★ 2026-07-13: 新增 image_urls 返回值（原型图视觉识别用）。
        ★ force_overwrite: 强制覆盖模式，跳过所有缓存检查。
        """
        from src.pipeline.steps.step1.mcp.confluence_mcp import get_confluence_client
        from src.core.db_manager import get_connection

        cf_ok = False
        cf_result = {}
        req_text = ""
        cf_changed = True
        old_req_text = ""
        cf_from_cache = False
        image_urls = []  # ★ 原型图 URL 列表
        cf_configured = bool(config.confluence_page_ids)

        if not cf_configured:
            _log.info("Confluence: 未配置，跳过")
            _log.info("  ○ Confluence: 未配置，跳过")
            return (
                cf_ok,
                req_text,
                cf_result,
                cf_changed,
                cf_from_cache,
                old_req_text,
                image_urls,
            )

        if run_id:
            _log.info("  [run_id=%s] Step 1: 正在拉取 Confluence...", run_id)

        _project_name = getattr(config, "project_name", "") or ""
        # ★ MySQL 缓存新鲜度判断 + 读取（取代磁盘 raw_confluence_pages_md）
        cache_fresh = False
        if not figma_force_refresh and not force_overwrite:
            with guard("_fetch_confluence: _conn = get_connection()", log=_log, level=logging.WARNING):
                _conn = get_connection()
                try:
                    _row = _conn.execute(
                        "SELECT content, updated_at FROM source_documents "
                        "WHERE project=%s AND doc_type='confluence' AND version_label=%s LIMIT 1",
                        (_project_name, _version_label),
                    ).fetchone()
                finally:
                    try:
                        _conn.close()
                    except Exception as _pass_e:
                        _log.warning(
                            "confluence.py:66 异常被吞: %s", _pass_e, exc_info=True
                        )
                if _row and _row["content"] and _row["updated_at"]:
                    _ut = _row["updated_at"]
                    _ts = (
                        _ut.timestamp()
                        if hasattr(_ut, "timestamp")
                        else time.mktime(_ut.timetuple())
                    )
                    if time.time() - _ts < _CACHE_TTL_SECONDS:
                        req_text = _row["content"]
                        if isinstance(req_text, (bytes, bytearray)):
                            req_text = req_text.decode("utf-8", "ignore")
                        body_start = req_text.find("\n---\n")
                        if body_start > 0:
                            req_text = req_text[body_start + 5 :].strip()
                        if req_text:
                            cache_fresh = True
                            cf_from_cache = True
                            cf_ok = True
                            _log.info(
                                "Confluence: MySQL 缓存新鲜(%.0fmin前)，跳过 API 调用 (%d 字符)",
                                (time.time() - _ts) / 60,
                                len(req_text),
                            )
                            _log.info(
                                "  ○ Confluence: MySQL 缓存新鲜(%.0fmin前)，跳过 API 调用 (%d 字符)"
                                % ((time.time() - _ts) / 60, len(req_text))
                            )

        if not cf_from_cache:
            _log.info("  ⏳ Confluence: 正在拉取...")
            _t0 = time.time()
            cf_client = get_confluence_client()
            cf_result = cf_client.fetch_confluence_all()
            _elapsed = time.time() - _t0
            cf_ok = cf_result.get("success", False)
            if cf_ok:
                _data = cf_result.get("data", {}) if isinstance(cf_result, dict) else {}
                # ★ REST 客户端返回 titles/page_ids，MCP 客户端返回 pages；兼容两者
                _pages = (
                    _data.get("pages")
                    or _data.get("titles")
                    or _data.get("page_ids")
                    or []
                )
                req_text = _data.get("text", "")
                # ★ 2026-07-13: 提取图片 URL（供视觉识别模块使用）
                image_urls = _data.get("image_urls", [])
                # ★ BUGFIX: 与缓存路径保持一致——统一剥离 \n---\n 前缀（元数据头）
                body_start = req_text.find("\n---\n")
                if body_start > 0:
                    req_text = req_text[body_start + 5 :].strip()
                # ★ S1-003: 内容标准化（5步确定性规则）— 哈希计算前统一文本格式，避免换行符差异误判变更
                from src.core.utils.text_utils import normalize_content

                req_text = normalize_content(req_text)
                _log.info(
                    "Confluence: PASS — %d 页 (%d 字符, %.1fs, 图片 %d 张)",
                    len(_pages),
                    len(req_text),
                    _elapsed,
                    len(image_urls),
                )
                _log.info(
                    "  ✓ Confluence: PASS — %d 页 (%d 字符, %.1fs, 图片 %d 张)",
                    len(_pages),
                    len(req_text),
                    _elapsed,
                    len(image_urls),
                )
            else:
                err_msg = cf_result.get("error", "未知错误")
                _log.error("Confluence: FAIL — %s (%.1fs)", err_msg, _elapsed)
                _log.error("  ✗ Confluence: FAIL — %s (%.1fs)", err_msg, _elapsed)

        # MySQL 化
        if cf_ok and req_text:
            _project_name = getattr(config, "project_name", "") or ""
            new_hash = hashlib.sha256(req_text.encode()).hexdigest()
            _existing_cf = None
            _conn_h = None
            try:
                _conn_h = get_connection()
                _cur_h = _conn_h.execute(
                    "SELECT content_hash FROM source_documents "
                    "WHERE project = %s AND doc_type = 'confluence' AND version_label = %s LIMIT 1",
                    (_project_name, _version_label),
                )
                _row_h = _cur_h.fetchone() if _cur_h else None
                if _row_h and _row_h.get("content_hash"):
                    _existing_cf = {"overall": _row_h["content_hash"]}

                old_hash = (
                    _existing_cf.get("overall", "")
                    if isinstance(_existing_cf, dict)
                    else ""
                )
                if old_hash and old_hash == new_hash:
                    cf_changed = False
                    _log.info("Confluence: 未变更 (哈希=%s)", new_hash[:8])
                    try:
                        _conn_h.close()
                    except Exception as _pass_e:
                        _log.warning(
                            "confluence.py:133 异常被吞: %s", _pass_e, exc_info=True
                        )
                    _conn_h = None
                else:
                    if old_hash:
                        _log.info(
                            "Confluence: 检测到变更 (旧=%s 新=%s)",
                            old_hash[:8],
                            new_hash[:8],
                        )
                    else:
                        _log.info("Confluence: 首次拉取 (哈希=%s)", new_hash[:8])
                    old_req_text = req_text
                    # ★ 复用同一连接写入（减少连接开销，保持读-写原子性）
                    self._write_confluence_to_mysql(
                        _project_name, _version_label, req_text, new_hash, conn=_conn_h
                    )
                    _conn_h = None  # _write_confluence_to_mysql 已负责关闭
            except Exception as _e:
                _log.warning("  [step1] 读取 Confluence 历史哈希失败: %s", _e)
            finally:
                if _conn_h:
                    try:
                        _conn_h.close()
                    except Exception as _pass_e:
                        _log.warning(
                            "confluence.py:149 异常被吞: %s", _pass_e, exc_info=True
                        )
        return (
            cf_ok,
            req_text,
            cf_result,
            cf_changed,
            cf_from_cache,
            old_req_text,
            image_urls,
        )

    def _write_confluence_to_mysql(
        self, project, version_label, req_text, new_hash, conn=None
    ):
        """★ 单连接原子写入 source_documents + source_hashes，失败 rollback。

        若调用方已持有连接（conn 参数），则复用该连接；否则自行获取。
        无论哪种情况，调用方不应再使用传入的 conn（本方法负责关闭自有连接，
        外部传入的连接由调用方管理）。
        """
        from src.core.db_manager import get_connection

        _conn = conn
        _own_conn = _conn is None
        try:
            if _conn is None:
                _conn = get_connection()
            # 1. source_documents（主表）
            # ★ 保存 source_url（原始 Confluence URL）
            from src.core.config_loader import config

            _cf_url = (config.CONFLUENCE_URL or "").strip()
            _conn.execute(
                "INSERT INTO source_documents (project, doc_type, version_label, content, content_hash, char_count, "
                " source_url, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW()) "
                "ON DUPLICATE KEY UPDATE content=VALUES(content), content_hash=VALUES(content_hash), "
                "char_count=VALUES(char_count), source_url=VALUES(source_url), updated_at=NOW()",
                (
                    project,
                    "confluence",
                    version_label,
                    req_text,
                    new_hash,
                    len(req_text),
                    _cf_url,
                ),
            )
            # 2. source_hashes 统一表（同一连接，原子性）
            _conn.execute(
                "INSERT INTO source_hashes (project, doc_type, version_label, content_hash, created_at, updated_at) "
                "VALUES (%s, 'confluence', %s, %s, NOW(), NOW()) "
                "ON DUPLICATE KEY UPDATE content_hash=VALUES(content_hash), updated_at=NOW()",
                (project, version_label, new_hash),
            )
            _conn.commit()
            _log.info(
                "  Confluence: 已原子写入 MySQL source_documents + source_hashes (%d 字符)",
                len(req_text),
            )
        except Exception as _e:
            _log.error("  [step1] Confluence 原子写入失败: %s", _e)
            if _conn:
                try:
                    _conn.rollback()
                except Exception as _pass_e:
                    _log.warning(
                        "confluence.py:187 异常被吞: %s", _pass_e, exc_info=True
                    )
        finally:
            if _own_conn and _conn:
                try:
                    _conn.close()
                except Exception as _pass_e:
                    _log.warning(
                        "confluence.py:191 异常被吞: %s", _pass_e, exc_info=True
                    )
        # ★ 写入 source_confluence_hashes 细表（Step2 增量对比使用，独立事务但非关键路径）
        with guard("_write_confluence_to_mysql: from src.pipeline.utils.source_hashe", log=_log, level=logging.WARNING):
            from src.pipeline.utils.source_hashes_store import source_hashes_store

            _ok = source_hashes_store.save_confluence_hash(
                overall_hash=new_hash,
                version_label=version_label,
            )
            if _ok:
                _log.info("  [step1] Confluence 细表哈希已写入")
            else:
                _log.error(
                    "  [step1] Confluence source_confluence_hashes 写入失败（详见上游错误日志）"
                )

    def _merge_uploaded_docs(self, req_text, project, version_label):
        """合并当前「项目 + 版本」分区的上传需求文档。返回 (req_text, upload_info)。"""
        uploaded_sources = []
        upload_info = {"count": 0, "formats": []}
        try:
            from src.pipeline.steps.step1.requirement_merger import (
                load_uploaded_requirements,
            )

            uploaded_sources = load_uploaded_requirements(project, version_label)
            if uploaded_sources:
                _log.info("  发现 %d 个上传的需求文档", len(uploaded_sources))
                _formats = []
                for src in uploaded_sources:
                    _log.info("    - %s (权重=%.1f)", src["source_type"], src["weight"])
                    _fmt = str(
                        src.get("source_type", "") or src.get("file_format", "") or ""
                    )
                    if _fmt:
                        _formats.append(_fmt)
                upload_info = {"count": len(uploaded_sources), "formats": _formats}
        except Exception as e:
            _log.warning("  加载上传文档失败: %s", e, exc_info=True)

        if uploaded_sources:
            from src.pipeline.steps.step1.requirement_merger import merge_requirements

            if req_text:
                # Confluence + 上传文档
                sources = [
                    {
                        "source_type": "confluence",
                        "text": req_text,
                        "weight": 1.0,
                        "metadata": {},
                    }
                ]
                sources.extend(uploaded_sources)
                req_text = merge_requirements(sources)
                _log.info(
                    "  需求文档已合并: Confluence + %d 个上传文件",
                    len(uploaded_sources),
                )
            else:
                # 仅上传文档（无 Confluence）
                req_text = merge_requirements(uploaded_sources)
                _log.info(
                    "  需求文档已合并: %d 个上传文件（无 Confluence）",
                    len(uploaded_sources),
                )

        return req_text, upload_info
