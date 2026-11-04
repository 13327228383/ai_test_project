# -*- coding: utf-8 -*-
"""
Step1DataFetcher 持久化与摘要 mixin

职责：
- _persist_requirements — 需求稿持久化到 MySQL
- _persist_figma — Figma UI 上下文持久化
- _build_summary — 构建结果摘要并打印
- _record_document_version — 记录文档版本
"""
# ★ P2: MySQL doc URI 前缀走 constants 集中点
from src.core.constants import MYSQL_DOC_URI_REQUIREMENT as _MYSQL_DOC_URI_REQ
from src.core.guarded import guard

import hashlib
import logging
import sys as _sys
from typing import Any

from src.core.config_loader import config

_log = logging.getLogger(__name__)
_TRUNC_REPORT = 60


class _Step1PersistMixin:
    """持久化与摘要能力。"""

    def _persist_requirements(self, cf_ok, req_text, cf_result, _version_label):
        """持久化处理后的需求稿为 source_documents + source_hashes（原子写入）。
        ★ fix: 当 Confluence 未配置但上传文档有内容时，仍需持久化 req_text。
        """
        if not req_text:
            return
        with guard("_persist_requirements: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
            from src.core.db_manager import get_connection

            _project_name = getattr(config, "project_name", "") or ""
            titles = (
                cf_result.get("data", {}).get("titles", [])
                if isinstance(cf_result, dict)
                else []
            )
            if cf_ok:
                header = "# 需求文档（Confluence）\n\n"
            else:
                header = "# 需求文档（上传文件）\n\n"
            if titles:
                header += "> 来源页面: %s\n\n" % ", ".join(titles)
            header += "---\n\n"
            # ★ S1-003: 内容标准化（5步确定性规则）— 哈希计算前统一文本格式，避免换行符差异误判变更
            from src.core.utils.text_utils import normalize_content

            req_text = normalize_content(req_text)
            full_content = header + req_text
            # 头部已是确定性的（不含生成时间）→ 哈希必须覆盖实际落库内容，
            # 否则同一份需求每次 Step1 都被判为"已变更"，且改动头部不会被察觉
            _hash = hashlib.sha256(full_content.encode()).hexdigest()

            # ★ 原子写入: source_documents + source_hashes 同一连接同一事务
            _conn = None
            try:
                _conn = get_connection()
                # 1. source_documents (confluence)
                _conn.execute(
                    "INSERT INTO source_documents (project, doc_type, version_label, content, content_hash, char_count, "
                    " created_at, updated_at) VALUES (%s, 'confluence', %s, %s, %s, %s, NOW(), NOW()) "
                    "ON DUPLICATE KEY UPDATE content=VALUES(content), content_hash=VALUES(content_hash), "
                    "char_count=VALUES(char_count), updated_at=NOW()",
                    (
                        _project_name,
                        _version_label,
                        full_content,
                        _hash,
                        len(full_content),
                    ),
                )
                # ★ 性能修复(07-05): 删除 doc_type='requirement' 双写
                #   原实现将同一份 full_content 同时写入 'confluence' 和 'requirement' 两个 doc_type，
                #   内容完全相同（同变量 full_content + _hash），无修改逻辑，纯冗余。
                #   下游唯一读取方 _downloads.py 已改为读 'confluence'。
                #   生命周期追踪通过 source_hashes 表 + 主键 (project, doc_type, version_label) 实现。
                # 3. source_hashes (confluence) — 与 source_documents 同一事务
                _conn.execute(
                    "INSERT INTO source_hashes "
                    "(project, doc_type, version_label, content_hash, created_at, updated_at) "
                    "VALUES (%s, 'confluence', %s, %s, NOW(), NOW()) "
                    "ON DUPLICATE KEY UPDATE content_hash = VALUES(content_hash), updated_at = NOW()",
                    (_project_name, _version_label or "", _hash),
                )
                _conn.commit()
                _log.info(
                    "  需求稿已原子写入 MySQL: source_documents(confluence) + source_hashes, %d 字符",
                    len(full_content),
                )
            except Exception as _e:
                if _conn:
                    with guard("_persist_requirements: _conn.rollback()", log=_log):
                        _conn.rollback()
                _log.warning("  [step1] 需求稿 MySQL 原子写入失败(已rollback): %s", _e)
            finally:
                if _conn:
                    with guard("_persist_requirements: _conn.close()", log=_log, level=logging.WARNING):
                        _conn.close()
            # 记录文档版本
            with guard("_persist_requirements: from src.users.manager import record", log=_log, level=logging.WARNING):
                from src.users.manager import record_document_version

                record_document_version(
                    _project_name,
                    "requirement",
                    # ★ P2: MySQL doc URI 走 constants
                    "%s%s" % (_MYSQL_DOC_URI_REQ, _version_label),
                    _hash,
                    len(req_text),
                )

    def _persist_sources_status(
        self, sources_status: dict[str, Any], _version_label: str
    ) -> None:
        """将 Step1 各数据源状态持久化到 source_documents，供前端步骤卡片展示。"""
        if not sources_status:
            return
        with guard("_persist_sources_status: import json as _json", log=_log, level=logging.WARNING):
            import json as _json
            from src.core.db_manager import get_connection, release_connection

            project = getattr(config, "project_name", "") or ""
            content = _json.dumps(sources_status, ensure_ascii=False)
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            conn = None
            try:
                conn = get_connection()
                conn.execute(
                    "INSERT INTO source_documents (project, doc_type, version_label, content, content_hash, char_count, "
                    "created_at, updated_at) VALUES (%s, 'step1_status', %s, %s, %s, CHAR_LENGTH(%s), NOW(), NOW()) "
                    "ON DUPLICATE KEY UPDATE content=VALUES(content), content_hash=VALUES(content_hash), "
                    "char_count=CHAR_LENGTH(VALUES(content)), updated_at=NOW()",
                    (project, _version_label or "", content, content_hash, content),
                )
                conn.commit()
                _log.info("  sources_status 已持久化到 source_documents(step1_status)")
            finally:
                if conn:
                    with guard("_persist_sources_status: release_connection(conn)", log=_log):
                        release_connection(conn)

    def _build_summary(
        self,
        cf_ok,
        cf_result,
        cf_configured,
        req_text,
        figma_ok,
        figma_configured,
        ui_context,
        cf_changed,
        figma_force_refresh,
        yapi_ok,
        api_docs_context,
        yapi_summary_text,
        test_accounts,
        db_configured,
        figma_result=None,
        db_status=None,
    ):
        """打印 Step1 执行摘要。优先展示具体指标（页数/接口数/节点数/表数），字符数辅助。"""
        _log.info("")
        _log.info("  ════════════════════════════════════")
        _log.info("  📊 Step 1 拉取结果")
        _log.info("  ════════════════════════════════════")

        # Confluence — 页数 + 字符数
        if cf_ok:
            _data = cf_result.get("data", {}) if isinstance(cf_result, dict) else {}
            _pages = (
                _data.get("pages") or _data.get("titles") or _data.get("page_ids") or []
            )
            _log.info(
                "    %-14s ✓ %d 页 (%d 字符)", "Confluence", len(_pages), len(req_text)
            )
        elif not cf_configured:
            _log.info("    %-14s ○ 未配置", "Confluence")
        else:
            err = (
                cf_result.get("error", "未知")
                if isinstance(cf_result, dict)
                else "未知"
            )
            _log.warning("    %-14s ✗ 失败 — %s", "Confluence", err[:_TRUNC_REPORT])

        # Figma — 页面数 + 节点数 + 字符数
        if figma_ok and ui_context:
            _figma_pages = (
                (figma_result or {}).get("total_pages", 0)
                if isinstance(figma_result, dict)
                else 0
            )
            _figma_nodes = (
                (figma_result or {})
                .get("layers", {})
                .get("layer3", {})
                .get("fetched_nodes", 0)
                if isinstance(figma_result, dict)
                else 0
            )
            _parts = []
            if _figma_pages:
                _parts.append("%d 页面" % _figma_pages)
            if _figma_nodes:
                _parts.append("%d 节点" % _figma_nodes)
            _parts.append("%d 字符" % len(ui_context))
            _log.info("    %-14s ✓ %s", "Figma", ", ".join(_parts))
        elif not figma_configured:
            _log.info("    %-14s ○ 未配置", "Figma")
        elif not cf_changed and not figma_force_refresh:
            _log.info("    %-14s ○ 跳过 (需求未变更)", "Figma")
        else:
            err = "未知"
            _log.warning("    %-14s ✗ 失败 — %s", "Figma", err[:_TRUNC_REPORT])

        # YApi — 接口数 + 项目名 + 字符数
        if yapi_ok and api_docs_context:
            # yapi_summary_text 格式: "项目: 深圳某业务系统, 接口总数: 217"
            _log.info(
                "    %-14s ✓ %s (%d 字符)",
                "YApi",
                yapi_summary_text,
                len(api_docs_context),
            )
        elif api_docs_context:
            _log.info("    %-14s △ 磁盘缓存 %d 字符", "YApi", len(api_docs_context))
        elif not config.api_docs_enabled:
            _log.info("    %-14s ○ 未配置", "YApi")
        else:
            _log.info("    %-14s ✗ 失败", "YApi")

        # 数据库 — 账号数 + 业务表数 + 字符数
        if test_accounts:
            _db_detail = (
                (db_status or {}).get("detail", "")
                if isinstance(db_status, dict)
                else ""
            )
            if _db_detail:
                _log.info(
                    "    %-14s ✓ %s (%d 字符)", "数据库", _db_detail, len(test_accounts)
                )
            else:
                _log.info("    %-14s ✓ %d 字符", "数据库", len(test_accounts))
        elif not db_configured:
            _log.info("    %-14s ○ 未配置", "数据库")
        else:
            _log.info("    %-14s ✗ 无数据", "数据库")

        # 导航地图已移除（2026-06-30: nav_map 整条链路删除）
        _log.info("  ════════════════════════════════════")
        _sys.stdout.flush()
