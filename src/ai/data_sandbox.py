# -*- coding: utf-8 -*-
"""
数据沙箱 (DataSandbox) — v5
============================
测试用例间数据隔离：快照、变更检测、回滚、重建。

隔离策略（config.DB_SANDBOX_MODE 控制）：
  "none"     — 不隔离（默认，向后兼容）
  "detect"   — 仅检测数据变更并 WARN
  "rollback" — 崖山 FLASHBACK 回滚（需 DBA 权限）
  "recreate" — 删除 TEST_ 前缀数据后重建

使用方式：
    from src.ai.data_sandbox import data_sandbox
    sandbox.pre_exec(mode="rollback")
    # ... 执行测试用例 ...
    sandbox.post_exec()
"""

import time
from src.core.guarded import guard
import hashlib
import json
import re
from typing import Any
import logging

_log = logging.getLogger(__name__)


class DataSandbox:
    """数据库沙箱：快照 + 变更检测 + 回滚。"""

    # 快照中要监控的关键表（为空则自动从 db_agent._TABLE_COLUMNS 获取）
    _MONITOR_TABLES: list = None

    def __init__(self):
        self._MONITOR_TABLES = []
        self._snapshot: dict[str, dict] = {}  # {table: {row_count, checksum}}
        self._mode: str = (
            "detect"  # ★ 安全修复：默认从 none 改为 detect（检测数据变更）
        )
        self._case_id: str = ""
        self._db_agent = None

    @property
    def mode(self) -> str:
        return self._mode

    def _get_db_agent(self):
        if self._db_agent is None:
            from src.extensions.db.db_agent import db_agent

            self._db_agent = db_agent
        return self._db_agent

    # ═══════════════════════════════════════════════
    # 测试用例分类
    # ═══════════════════════════════════════════════

    @staticmethod
    def classify_intent(intent: dict) -> str:
        """
        根据测试用例的 db_checks / steps 判断类型。

        返回: "readonly" | "mutation"
        """
        # 检查 case_actions_json 中的 db_checks
        steps = intent.get("case_actions_json", "")
        combined = str(steps)

        mutation_keywords = [
            "新增",
            "创建",
            "添加",
            "写入",
            "INSERT",
            "插入",
            "删除",
            "移除",
            "DELETE",
            "DROP",
            "修改",
            "更新",
            "编辑",
            "UPDATE",
            "变更",
            "导入",
            "上传",
            "import",
            "upload",
        ]

        return (
            "mutation"
            if any(kw in combined for kw in mutation_keywords)
            else "readonly"
        )

    @staticmethod
    def sort_intents(intents: list[dict]) -> list[dict]:
        """对测试用例排序：readonly 先，mutation 后。"""
        readonly = [c for c in intents if DataSandbox.classify_intent(c) == "readonly"]
        mutation = [c for c in intents if DataSandbox.classify_intent(c) == "mutation"]
        return readonly + mutation

    # ═══════════════════════════════════════════════
    # 快照
    # ═══════════════════════════════════════════════

    def pre_exec(self, mode: str = "none", case_id: str = "") -> dict[str, Any]:
        """执行前准备：根据模式创建快照。"""
        self._mode = mode
        self._case_id = case_id
        self._snapshot = {}

        if mode == "none":
            return {"success": True, "mode": "none"}

        try:
            db = self._get_db_agent()
            tables = self._get_monitor_tables(db)

            for table in tables:
                with guard("pre_exec: safe_t = self._sanitize_table_name(t", log=_log, level=logging.INFO):
                    safe_t = self._sanitize_table_name(table)
                    if not safe_t:
                        continue
                    q = self._quote(db)
                    r = db.query("SELECT COUNT(*) AS cnt FROM %s%s%s" % (q, safe_t, q))
                    row_count = 0
                    if r.get("success") and r.get("data"):
                        row_count = (
                            r["data"][0].get("CNT", 0)
                            if isinstance(r["data"][0], dict)
                            else 0
                        )

                    # 采样计算 checksum
                    checksum = self._compute_checksum(db, table)

                    self._snapshot[table] = {
                        "row_count": row_count,
                        "checksum": checksum,
                        "timestamp": time.time(),
                    }

            _log.info(
                "  [Sandbox] 快照完成: %d 表 (mode=%s)", len(self._snapshot), mode
            )
            return {"success": True, "mode": mode, "tables": len(self._snapshot)}

        except Exception as e:
            _log.info("  [Sandbox] 快照异常: %s", e)
            return {"success": False, "error": str(e)}

    def post_exec(self) -> dict[str, Any]:
        """执行后检查：对比快照，按模式处理。"""
        if self._mode == "none" or not self._snapshot:
            return {"success": True, "mode": "none"}

        try:
            db = self._get_db_agent()
            changes = []

            for table, snap in self._snapshot.items():
                try:
                    safe_t = self._sanitize_table_name(table)
                    if not safe_t:
                        continue
                    q = self._quote(db)
                    r = db.query("SELECT COUNT(*) AS cnt FROM %s%s%s" % (q, safe_t, q))
                    new_count = 0
                    if r.get("success") and r.get("data"):
                        new_count = (
                            r["data"][0].get("CNT", 0)
                            if isinstance(r["data"][0], dict)
                            else 0
                        )

                    old_count = snap["row_count"]
                    if new_count != old_count:
                        changes.append(
                            {
                                "table": table,
                                "old_count": old_count,
                                "new_count": new_count,
                                "delta": new_count - old_count,
                            }
                        )
                except Exception:
                    _log.warning(
                        "  [Sandbox] 表 %s 变更检测失败，跳过", table, exc_info=True
                    )
                    continue

            if changes:
                change_summary = ", ".join(
                    "%s(%d→%d)" % (c["table"], c["old_count"], c["new_count"])
                    for c in changes[:10]
                )
                _log.info("  [Sandbox] 检测到数据变更: %s", change_summary)

                if self._mode == "rollback":
                    self._do_rollback(db, changes)
                elif self._mode == "recreate":
                    self._do_recreate(db)
                # detect 模式只 WARN，不做任何操作

            return {
                "success": True,
                "mode": self._mode,
                "changes": changes,
                "rolled_back": self._mode in ("rollback", "recreate"),
            }

        except Exception as e:
            _log.info("  [Sandbox] post_exec 异常: %s", e)
            return {"success": False, "error": str(e)}

    # ═══════════════════════════════════════════════
    # 回滚策略
    # ═══════════════════════════════════════════════

    def _do_rollback(self, db, changes: list[dict]):
        """崖山 FLASHBACK 回滚变更的表。"""
        with guard("_do_rollback: for c in changes: table = c['table']", log=_log, level=logging.INFO):
            for c in changes:
                table = c["table"]
                ts = time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(self._snapshot[table]["timestamp"]),
                )
                with guard("_do_rollback: result = db.flashback_table(table, t", log=_log, level=logging.INFO):
                    result = db.flashback_table(table, ts)
                    if result.get("success"):
                        _log.info("  [Sandbox] FLASHBACK %s → %s ✓", table, ts)
                    else:
                        _log.error(
                            "  [Sandbox] FLASHBACK %s 失败: %s",
                            table,
                            result.get("error", "?"),
                        )

    def _do_recreate(self, db):
        """重建模式：清理 TEST_ 前缀数据。"""
        with guard("_do_recreate: tables = list(self._snapshot.keys())", log=_log, level=logging.INFO):
            tables = list(self._snapshot.keys())
            for table in tables:
                try:
                    safe_t = self._sanitize_table_name(table)
                    if not safe_t:
                        continue
                    q = self._quote(db)
                    sql = (
                        "DELETE FROM %s%s%s WHERE CODE LIKE 'TEST_%%' OR NAME LIKE 'TEST_%%'"
                        % (
                            q,
                            safe_t,
                            q,
                        )
                    )
                    r = db.execute(sql)
                    if r.get("success") and r.get("rows_affected", 0) > 0:
                        _log.info(
                            "  [Sandbox] 清理 %s: %d 行 TEST_ 数据",
                            table,
                            r["rows_affected"],
                        )
                except Exception:
                    _log.warning(
                        "  [Sandbox] 表 %s 变更检测失败，跳过", table, exc_info=True
                    )
                    continue

    # ═══════════════════════════════════════════════
    # 工具方法
    # ═══════════════════════════════════════════════

    def _get_monitor_tables(self, db) -> list[str]:
        """获取需要监控的表列表。"""
        if self._MONITOR_TABLES:
            return self._MONITOR_TABLES

        # 自动从 db_agent 获取（排除纯日志/审计表）
        if hasattr(db, "_TABLE_COLUMNS") and db._TABLE_COLUMNS:
            all_tables = list(db._TABLE_COLUMNS.keys())
        else:
            # 尝试查询
            try:
                db_type = (db._db_type or "").lower()
                if db_type in ("yasdb", "oracle"):
                    r = db.query("SELECT TABLE_NAME FROM USER_TABLES")
                elif db_type == "mysql":
                    r = db.query("SHOW TABLES")
                else:
                    r = db.query("SELECT name FROM sqlite_master WHERE type='table'")
                if r.get("success"):
                    all_tables = [list(row.values())[0] for row in r["data"]]
                else:
                    return []
            except Exception as _e:
                _log.debug("监控表列表查询失败: %s", _e, exc_info=True)
                return []

        # 排除日志/审计/历史表
        exclude_kw = [
            "LOG_",
            "_LOG",
            "AUDIT",
            "HISTORY",
            "_BAK",
            "_TMP",
            "TMP_",
            "QRTZ_",
            "SCHEDULER",
            "FLYWAY",
        ]
        tables = [
            str(t)
            for t in all_tables
            if not any(kw in str(t).upper() for kw in exclude_kw)
        ]
        return tables[:30]  # 最多监控 30 张表

    @staticmethod
    def _quote(db) -> str:
        """根据数据库类型返回标识符引号。"""
        with guard("_quote: dt = (db._db_type or '').lower()", log=_log):
            dt = (db._db_type or "").lower()
            if dt == "mysql":
                return "`"
        return '"'

    @staticmethod
    def _sanitize_table_name(name: str) -> str:
        """验证表名合法性：仅允许字母/数字/下划线且以字母或下划线开头。不合法返回空串。"""
        if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", str(name)):
            return str(name)
        return ""

    @staticmethod
    def _compute_checksum(db, table: str) -> str:
        """对表前 100 行计算轻量 checksum。"""
        with guard("_compute_checksum: safe_t = DataSandbox._sanitize_table", log=_log):
            safe_t = DataSandbox._sanitize_table_name(table)
            if not safe_t:
                return ""
            q = DataSandbox._quote(db)
            lim = (
                "FETCH FIRST 100 ROWS ONLY"
                if (db._db_type or "").lower() in ("yasdb", "oracle")
                else "LIMIT 100"
            )
            r = db.query("SELECT * FROM %s%s%s %s" % (q, safe_t, q, lim))
            if r.get("success") and r.get("data"):
                raw = json.dumps(r["data"], sort_keys=True, default=str)
                return hashlib.md5(raw.encode()).hexdigest()
        return ""


# 单例
data_sandbox = DataSandbox()
