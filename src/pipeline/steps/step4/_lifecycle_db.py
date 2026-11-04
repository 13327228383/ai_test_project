# -*- coding: utf-8 -*-
"""TDL DB 受控通道（T04，W2 P0-2）—— 白名单双闸门 + 参数化写 + 精确回滚。

★ 设计约束（C3）：严禁放宽/复用 db_verifier.safety._validate_sql_safe —— 那是只读安全阀，
  放宽会让全仓测试 SQL 都被拦。本模块是生命周期 DB 写的**独立受控通道**：
  - 白名单双闸门：TDL_DB_WRITE_ENABLED（默认 False）+ TDL_DB_TABLE_WHITELIST（默认空）
  - 参数化：INSERT/DELETE 值一律参数化，表名/列名做标识符合法性校验
  - 精确回滚：只按 setup 记录的主键删除；ref 空一律拒绝（严禁按条件批删真实数据）

★ §14 关键改动 [已复核-DeepSeek-V4-Flash 2026-08-05]：
  DB 写 + 3 文件，W2 最高风险项；未经过强模型（GLM/DeepSeek）复核，请勿视为已审核。
  ★ F1 修复（2026-08-05 W3）：db_seed 主键列强制校验 —— capture 未指定时默认 'id' 也
    必须经 information_schema 校验确为该表 PRIMARY/UNIQUE 键；校验失败（含查询异常）一律
    拒绝放行（fail-closed）。修复"表自增主键非 id 且另有 id 列时 DELETE 删错行"。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from src.core.pipeline_config import _read_config_value as _cfg

_log = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def assert_table_allowed(table: str) -> bool:
    """TDL_DB_WRITE_ENABLED 且 table ∈ TDL_DB_TABLE_WHITELIST 才放行；否则 False（双闸门）。"""
    if not _cfg("TDL_DB_WRITE_ENABLED", False):
        return False
    whitelist = str(_cfg("TDL_DB_TABLE_WHITELIST", "") or "")
    allowed = {t.strip().upper() for t in whitelist.split(",") if t.strip()}
    return bool(table) and str(table).strip().upper() in allowed


def _substitute_ns(value: Any, ns: str) -> Any:
    """递归替换 ${ns} 占位符（实现数据隔离命名空间）。"""
    if isinstance(value, str):
        return value.replace("${ns}", ns)
    if isinstance(value, dict):
        return {k: _substitute_ns(v, ns) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_ns(v, ns) for v in value]
    return value


def _validate_ident(name: str) -> bool:
    """标识符合法性校验（防 SQL 注入，仅字母/数字/下划线且字母/下划线开头）。"""
    return bool(_IDENT_RE.match(str(name or "").strip()))


def _resolve_pk_col(capture: dict | None) -> str:
    """从 capture 解析回滚主键列；capture 为空或全空值 → 返回 'id'（由 db_seed 校验放行）。

    ★ F1（2026-08-05）：不再无条件信任默认 'id' —— 返回值仅作候选，
       db_seed 必须经 _is_primary_or_unique_key 校验后才放行。
    """
    if isinstance(capture, dict):
        for _v in capture.values():
            if str(_v or "").strip():
                return str(_v).strip()
    return "id"


def _is_primary_or_unique_key(table: str, pk_col: str) -> bool:
    """校验 pk_col 确为该表 PRIMARY/UNIQUE 键（information_schema，只读）。

    查询失败 / 表或列不存在 / 校验不通过 → False（fail-closed：宁可拒绝，不可删错行）。
    """
    if not _validate_ident(table) or not _validate_ident(pk_col):
        return False
    try:
        from src.extensions.db.db_agent import db_agent

        sql = (
            "SELECT 1 FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s "
            "AND (CONSTRAINT_NAME = 'PRIMARY' OR "
            "     CONSTRAINT_NAME IN ("
            "       SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
            "       WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "       AND CONSTRAINT_TYPE = 'UNIQUE')) "
            "LIMIT 1"
        )
        res = db_agent.query(sql, (table, pk_col, table))
        return bool(res.get("data"))
    except Exception as e:  # noqa: BLE001 —— fail-closed：查询异常一律拒绝
        _log.warning(
            "[TDL] 主键列校验查询异常（fail-closed 拒绝）: %s: %s", type(e).__name__, e
        )
        return False


def db_seed(
    table: str, payload: dict, ns: str, capture: dict | None = None
) -> dict[str, Any]:
    """白名单校验 → ${ns} 占位替换 → db_agent.insert() → 返回 {"ok","ref":{pk:val}}。

    ref 主键列优先级：capture 指定的列（值=lastrowid）→ 缺省 'id'；
    ★ F1（2026-08-05）：无论 capture 是否指定，pk_col 必须经
       _is_primary_or_unique_key（information_schema）校验为该表 PRIMARY/UNIQUE 键，
       校验失败拒绝放行（防"自增主键非 id 且另有 id 列"时回滚删错行）。
    """
    table = str(table or "").strip()
    if not assert_table_allowed(table):
        return {
            "ok": False,
            "ref": {},
            "error": f"表 {table} 不在白名单（TDL_DB_WRITE_ENABLED/TDL_DB_TABLE_WHITELIST 双闸门）",
        }
    if not _validate_ident(table):
        return {"ok": False, "ref": {}, "error": f"非法表名: {table}"}
    if not isinstance(payload, dict) or not payload:
        return {"ok": False, "ref": {}, "error": "payload 为空"}
    data = _substitute_ns(dict(payload), ns)
    try:
        from src.extensions.db.db_agent import db_agent

        res = db_agent.insert(table, data)
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "ref": {},
            "error": f"insert 异常: {type(e).__name__}: {e}",
        }
    if not res.get("success"):
        return {
            "ok": False,
            "ref": {},
            "error": str(res.get("error") or "insert_failed"),
        }
    lastrowid = res.get("lastrowid")
    if lastrowid is None:
        return {
            "ok": False,
            "ref": {},
            "error": "insert 未返回 lastrowid（无法精确回滚，拒绝放行）",
        }
    pk_col = _resolve_pk_col(capture)
    if not _validate_ident(pk_col):
        return {
            "ok": False,
            "ref": {},
            "error": f"主键列名非法: {pk_col}（必须 capture 指定合法主键列）",
        }
    # ★ F1：主键列必须确为该表 PRIMARY/UNIQUE 键，否则拒绝（防回滚删错行）
    if not _is_primary_or_unique_key(table, pk_col):
        return {
            "ok": False,
            "ref": {},
            "error": (
                f"主键列 {pk_col} 不是表 {table} 的 PRIMARY/UNIQUE 键（或校验失败），"
                "拒绝放行；请 capture 指定真实主键列"
            ),
        }
    return {"ok": True, "ref": {pk_col: lastrowid}, "lastrowid": lastrowid}


def db_rollback(table: str, ref: dict[str, Any]) -> dict[str, Any]:
    """★ 仅按 setup 记录的主键精确删除；ref 为空一律拒绝（严禁按条件批删真实数据）。"""
    table = str(table or "").strip()
    if not assert_table_allowed(table):
        return {
            "ok": False,
            "deleted": 0,
            "error": f"表 {table} 不在白名单（拒绝回滚）",
        }
    if not _validate_ident(table):
        return {"ok": False, "deleted": 0, "error": f"非法表名: {table}"}
    if not ref or not isinstance(ref, dict) or len(ref) != 1:
        return {
            "ok": False,
            "deleted": 0,
            "error": "ref 必须恰含一个主键条件（空/多条件一律拒绝，严禁按条件批删）",
        }
    col, val = next(iter(ref.items()))
    col = str(col or "").strip()
    if not _validate_ident(col):
        return {"ok": False, "deleted": 0, "error": f"非法主键列名: {col}"}
    try:
        from src.extensions.db.db_agent import db_agent

        sql = f"DELETE FROM `{table}` WHERE `{col}` = %s"
        res = db_agent.execute(sql, (val,))
        deleted = int(res.get("rows_affected", 0) or 0)
        return {"ok": True, "deleted": deleted}
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "deleted": 0,
            "error": f"回滚异常: {type(e).__name__}: {e}",
        }
