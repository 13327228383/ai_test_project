# -*- coding: utf-8 -*-
"""
Step1DataFetcher 数据库查询 mixin

职责：
- _fetch_database — 查询数据库真实测试账号 + DB Schema 并持久化到 source_documents

★ : 修复"假ok"阻断（AGENTS.md §13.1 验收标准③）
  - _version_label 参数化（与 yapi/persist 一致），消除 config.version_label 空值导致 INSERT 静默跳过
  - 新增状态-落库一致性断言（INSERT 后 SELECT 复核 doc_type='database' 行真实存在）
  - 异常改阻断（warning→error+fail），消除"配置但失败→降级warning"反模式（§5）
  - 状态判定与落库事实绑定：schema 非空但未落库 → status=failed（消除假ok）
"""

import hashlib
from src.core.guarded import guard
import logging

from src.core.config_loader import config

_log = logging.getLogger(__name__)
_TRUNC_DETAIL = 80
_TRUNC_STATUS = 100


class _Step1DatabaseMixin:
    """数据库查询能力。"""

    def _fetch_database(
        self, _project="", _version_label=""
    ) -> tuple[str, dict[str, str]]:
        """查询数据库测试账号 + DB Schema，返回 (test_accounts, db_status)。

        ★ v5: _project 与 _version_label 均从调用方显式传入（与 yapi/persist 一致）。
              禁止从 config.project_name / config.version_label 取（config 无这两个公有属性
              → getattr 返回 '' → INSERT 被静默跳过 → 假ok）。project 由 step1 线程上下文
              ctx["project"] 提供，version_label 由 fetcher._setup_version() 提供，均为可信来源。
        """
        test_accounts = ""
        db_status = {"status": "skipped", "detail": "未配置数据库连接信息"}

        if not config.db_enabled:
            _log.info("数据库: 未启用 DB_ENABLED，跳过")
            return test_accounts, db_status

        _ver_label = _version_label or ""
        _project = _project or ""

        # ★ DB Schema 拉取 + 持久化（状态-落库一致性断言）
        _schema_text, _schema_persisted, _schema_meta = self._fetch_and_persist_schema(
            _project, _ver_label
        )

        # ★ 测试账号（不依赖落库，仅内存上下文）
        test_accounts = self._fetch_test_accounts()

        # ★ 状态判定：与落库事实绑定（消除假ok）
        db_status = self._build_db_status(
            test_accounts, _schema_text, _schema_persisted, _schema_meta
        )
        return test_accounts, db_status

    def _fetch_and_persist_schema(self, _project: str, _ver_label: str):
        """拉取 DB Schema 并持久化，返回 (schema_text, persisted, meta)。"""
        _schema_text = ""
        _persisted = False
        _meta = {"table_count": 0, "rel_count": 0, "field_count": 0}
        try:
            from src.core.session_context import session_ctx

            _schema_text = session_ctx.for_traceability()
            if not _schema_text:
                _log.debug("DB Schema: for_traceability() 返回空，跳过持久化")
                return _schema_text, _persisted, _meta
            _meta["table_count"] = sum(
                1
                for line in _schema_text.splitlines()
                if line.strip().startswith("- ") and ":" in line
            )
            _meta["rel_count"] = sum(
                1
                for line in _schema_text.splitlines()
                if "→" in line and any(c in line for c in ("1:N", "N:1", "N:M"))
            )
            _meta["field_count"] = sum(
                1
                for line in _schema_text.splitlines()
                if line.startswith("  - ") and ":" in line
            )
            if not (_project and _ver_label):
                _log.error(
                    "DB Schema: project/version_label 缺失(project=%s, ver=%s)，无法持久化",
                    _project,
                    _ver_label,
                )
                return _schema_text, _persisted, _meta
            _persisted = self._persist_schema_row(_project, _ver_label, _schema_text)
            if _persisted:
                _log.info(
                    "DB Schema: %d 表 / %d 关系已持久化到 source_documents (%d 字符)",
                    _meta["table_count"],
                    _meta["rel_count"],
                    len(_schema_text),
                )
        except Exception as _schema_e:
            _log.error(
                "DB Schema 拉取/持久化失败(阻断): %s",
                str(_schema_e)[:80],
                exc_info=True,
            )
        return _schema_text, _persisted, _meta

    def _persist_schema_row(
        self, _project: str, _ver_label: str, _schema_text: str
    ) -> bool:
        """INSERT DB Schema 到 source_documents，SELECT 复核落库，返回是否成功。

        ★ 状态-落库一致性断言：INSERT 后 SELECT 复核行真实存在，
          消除 ON DUPLICATE 静默跳过 / rowcount 语义歧义（1=insert/2=update/0=无变化）。
        """
        from src.core.db_manager import get_connection

        _conn = None
        try:
            _conn = get_connection()
            _conn.execute(
                "INSERT INTO source_documents "
                "(project, doc_type, version_label, content, content_hash, char_count, weight, category) "
                "VALUES (%s, 'database', %s, %s, %s, %s, 0.80, 'database') "
                "ON DUPLICATE KEY UPDATE content=VALUES(content), content_hash=VALUES(content_hash), "
                "char_count=VALUES(char_count), updated_at=NOW()",
                (
                    _project,
                    _ver_label,
                    _schema_text,
                    hashlib.sha256(_schema_text.encode()).hexdigest(),
                    len(_schema_text),
                ),
            )
            _conn.commit()
            # ★ 状态-落库一致性断言：SELECT 复核行真实存在
            _row = _conn.execute(
                "SELECT char_count FROM source_documents "
                "WHERE project=%s AND doc_type='database' AND version_label=%s LIMIT 1",
                (_project, _ver_label),
            ).fetchone()
            if _row and _row["char_count"] and int(_row["char_count"]) > 0:
                return True
            _log.error(
                "DB Schema: INSERT 后 SELECT 复核未找到落库行(project=%s, ver=%s)",
                _project,
                _ver_label,
            )
            return False
        except Exception as _e:
            _log.error("DB Schema 持久化失败(阻断): %s", str(_e)[:80], exc_info=True)
            if _conn:
                with guard("_persist_schema_row: _conn.rollback()", log=_log):
                    _conn.rollback()
            return False
        finally:
            if _conn:
                with guard("_persist_schema_row: _conn.close()", log=_log):
                    _conn.close()

    def _fetch_test_accounts(self) -> str:
        """查询测试账号文本。"""
        try:
            from src.core.session_context import session_ctx

            return session_ctx.get_test_accounts()
        except Exception as e:
            _log.error("测试账号查询失败: %s", e, exc_info=True)
            return ""

    def _build_db_status(
        self,
        test_accounts: str,
        _schema_text: str,
        _schema_persisted: bool,
        _meta: dict,
    ) -> dict:
        """构建 db_status，状态与落库事实绑定。"""
        # ★ 状态-落库一致性：schema 非空但未落库 → failed（消除假ok）
        if _schema_text and not _schema_persisted:
            _st = {
                "status": "failed",
                "detail": "Schema拉取成功(%d字符)但未落库到source_documents"
                % len(_schema_text),
                "schema_chars": len(_schema_text),
                "schema_persisted": False,
            }
            _log.error("数据库: 假ok已消除 — %s", _st["detail"])
            return _st
        if not (test_accounts or _schema_text):
            _log.warning("数据库查询为空，测试用例将使用通用描述")
            return {"status": "ok", "detail": "查询为空，使用通用描述"}

        if test_accounts:
            self._test_accounts_context = test_accounts

        _account_count, _entity_table_count = self._resolve_system_meta()
        _detail_parts = []
        if _meta["table_count"] > 0:
            _detail_parts.append("%d 表" % _meta["table_count"])
        elif _entity_table_count > 0:
            _detail_parts.append("%d 业务表" % _entity_table_count)
        if _meta["field_count"] > 0:
            _detail_parts.append("%d 字段" % _meta["field_count"])
        if _meta["rel_count"] > 0:
            _detail_parts.append("%d 关系" % _meta["rel_count"])
        if _account_count > 0:
            _detail_parts.append("%d 账号" % _account_count)
        if not _detail_parts:
            _detail = "%d 字符" % max(len(test_accounts), len(_schema_text))
        else:
            _detail = ", ".join(_detail_parts)
        _log.info(
            "数据库: %s (账号文本 %d 字符 / Schema %d 字符 / 落库=%s)",
            _detail,
            len(test_accounts),
            len(_schema_text),
            _schema_persisted,
        )
        return {
            "status": "ok",
            "detail": _detail,
            "account_count": _account_count,
            "table_count": _meta["table_count"] or _entity_table_count,
            "relationship_count": _meta["rel_count"],
            "field_count": _meta["field_count"],
            "schema_chars": len(_schema_text),
            "schema_persisted": _schema_persisted,
        }

    def _resolve_system_meta(self) -> tuple:
        """解析系统数据目录，返回 (account_count, entity_table_count)。"""
        try:
            from src.extensions.system_data_resolver import system_data

            _catalog = system_data.resolve()
            _account_count = len(_catalog.get("accounts", []) or [])
            _entity_tables = (
                _catalog.get("classification", {}).get("entity_tables", []) or []
            )
            return _account_count, len(_entity_tables)
        except Exception as _e:
            _log.debug("[database] 系统数据目录解析异常: %s", _e, exc_info=True)
            return 0, 0
