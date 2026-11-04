# -*- coding: utf-8 -*-
"""数据库 Schema 初始化模块 — 从 db_manager 拆分而来。"""

import re
from src.core.guarded import guard
import threading
from pathlib import Path

import pymysql
import logging

_log = logging.getLogger(__name__)

_schema_done = False
_schema_lock = threading.Lock()


# ── ALTER 语句预检查正则（用于解析语句类型和目标对象名）──
_RE_ALTER_TABLE = re.compile(r"ALTER\s+TABLE\s+`?(\w+)`?", re.IGNORECASE)
_RE_ADD_COLUMN = re.compile(r"ADD\s+(?:COLUMN\s+)?`?(\w+)`?", re.IGNORECASE)
_RE_ADD_INDEX = re.compile(r"ADD\s+INDEX\s+`?(\w+)`?", re.IGNORECASE)
_RE_DROP_FK = re.compile(r"DROP\s+FOREIGN\s+KEY\s+`?(\w+)`?", re.IGNORECASE)
_RE_MODIFY = re.compile(r"MODIFY\s+COLUMN\s+`?(\w+)`?", re.IGNORECASE)
# MODIFY 的目标类型（用于幂等判据：现有 DATA_TYPE 与之相同即可跳过）
_RE_MODIFY_TYPE = re.compile(
    r"MODIFY\s+(?:COLUMN\s+)?`?(\w+)`?\s+([A-Za-z]+)", re.IGNORECASE
)


def _alter_already_applied(cur, db_name: str, stmt: str) -> bool:
    """检查 ALTER 语句的目标对象是否已存在，存在则可跳过执行。

    支持的 ALTER 类型:
    - ADD COLUMN: 查 information_schema.columns，列已存在则跳过
    - ADD INDEX:  查 information_schema.statistics，索引已存在则跳过
    - DROP FOREIGN KEY: 查 information_schema.table_constraints，外键不存在则跳过
    - MODIFY COLUMN: 查 information_schema.columns，现有 DATA_TYPE 与目标类型
      一致则跳过（避免每次启动重建表）
    - 其他: 不预检查，返回 False（始终执行）

    返回 True 表示可跳过，False 表示需执行。
    """
    m_tbl = _RE_ALTER_TABLE.search(stmt)
    if not m_tbl:
        return False
    tbl = m_tbl.group(1)

    # DROP FOREIGN KEY — 外键已不存在则跳过
    m_drop_fk = _RE_DROP_FK.search(stmt)
    if m_drop_fk:
        fk_name = m_drop_fk.group(1)
        try:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM information_schema.table_constraints "
                "WHERE table_schema=%s AND table_name=%s "
                "AND constraint_name=%s AND constraint_type='FOREIGN KEY'",
                (db_name, tbl, fk_name),
            )
            row = cur.fetchone()
            return bool(row and int(row.get("cnt", 0) or 0) == 0)
        except Exception as _e:
            _log.debug("[DB] DROP FK 预检查失败，不跳过: %s", _e)
            return False

    # ADD COLUMN — 列已存在则跳过
    m_add_col = _RE_ADD_COLUMN.search(stmt)
    if m_add_col:
        col_name = m_add_col.group(1)
        # 排除 ADD INDEX 误匹配（ADD COLUMN 正则不会匹配 ADD INDEX，但防御性检查）
        if col_name.upper() in (
            "INDEX",
            "KEY",
            "PRIMARY",
            "UNIQUE",
            "FOREIGN",
            "CONSTRAINT",
        ):
            pass
        else:
            try:
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name=%s AND column_name=%s",
                    (db_name, tbl, col_name),
                )
                row = cur.fetchone()
                return bool(row and int(row.get("cnt", 0) or 0) > 0)
            except Exception as _e:
                _log.debug("[DB] ADD COLUMN 预检查失败，不跳过: %s", _e)
                return False

    # ADD INDEX — 索引已存在则跳过
    m_add_idx = _RE_ADD_INDEX.search(stmt)
    if m_add_idx:
        idx_name = m_add_idx.group(1)
        try:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM information_schema.statistics "
                "WHERE table_schema=%s AND table_name=%s AND index_name=%s",
                (db_name, tbl, idx_name),
            )
            row = cur.fetchone()
            return bool(row and int(row.get("cnt", 0) or 0) > 0)
        except Exception as _e:
            _log.debug("[DB] ADD INDEX 预检查失败，不跳过: %s", _e)
            return False

    # MODIFY COLUMN — 目标类型与现有类型一致则跳过
    # ★ 根因修复(2026-10-28)：原实现把 MODIFY 归入"其他类型，不预检查，始终执行"，
    #   于是每次启动都会对目标表重建（executions 等大表尤为明显）。MODIFY 的幂等
    #   判据本就是"目标列类型是否已经达标"，按 information_schema.DATA_TYPE 比对即可。
    m_modify = _RE_MODIFY_TYPE.search(stmt)
    if m_modify:
        col_name, target_type = m_modify.group(1), m_modify.group(2).upper()
        try:
            cur.execute(
                "SELECT DATA_TYPE AS dt FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name=%s AND column_name=%s",
                (db_name, tbl, col_name),
            )
            row = cur.fetchone()
            cur_type = str((row or {}).get("dt") or "").upper()
            return bool(cur_type) and cur_type == target_type
        except Exception as _e:
            _log.debug("[DB] MODIFY 预检查失败，不跳过: %s", _e)
            return False

    # 其他类型 — 不预检查，始终执行
    return False


def _init_schema(conn: pymysql.Connection) -> None:
    """初始化数据库表结构（仅首次调用执行，线程安全）。

    策略：
      - 解析 schema.sql 中所有 CREATE TABLE 语句
      - 一次查询 information_schema 获取已存在的表
      - 只对缺失的表执行 CREATE TABLE（增量 DDL）
      - 已全量存在 → 跳过 DDL（~1ms）
      - INSERT 语句始终执行（IGNORE 保证幂等）
      - seed + migrations 始终执行
    """
    global _schema_done
    if _schema_done:
        return
    with _schema_lock:
        if _schema_done:
            return

        sp = Path(__file__).resolve().parent / "schema.sql"
        if not sp.is_file():
            _schema_done = True
            return
        raw = sp.read_text(encoding="utf-8")

        # ── 1. 提取 schema.sql 中所有表名 ──
        _TABLE_NAME_RE = re.compile(
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+`?(\w+)`?\s*\(",
            re.IGNORECASE,
        )
        schema_tables = set(_TABLE_NAME_RE.findall(raw))
        if not schema_tables:
            _log.warning("[DB] schema.sql 未找到 CREATE TABLE 语句")
            _schema_done = True
            return

        # ── 2. 一次查询获取所有已存在的表 ──
        existing_tables: set = set()
        try:
            cur_chk = conn.cursor()
            cur_chk.execute("SELECT DATABASE()")
            row = cur_chk.fetchone()
            db_name = (row or {}).get("DATABASE()") or None
            cur_chk.close()
            if db_name:
                placeholders = ",".join(["%s"] * len(schema_tables))
                cur_chk = conn.cursor()
                cur_chk.execute(
                    f"SELECT TABLE_NAME FROM information_schema.TABLES "
                    f"WHERE TABLE_SCHEMA=%s AND TABLE_NAME IN ({placeholders})",
                    (db_name, *schema_tables),
                )
                existing_tables = {r["TABLE_NAME"] for r in (cur_chk.fetchall() or [])}
                cur_chk.close()
        except Exception as _e:
            _log.warning("[DB] 表存在性检查失败，将执行完整 DDL: %s", _e)
            existing_tables = set()  # 空集 → 所有表都会被视为缺失

        missing_tables = schema_tables - existing_tables

        # ── 3. 增量 DDL：只对缺失的表执行 CREATE TABLE ──
        raw_clean = re.sub(r"/\*[\s\S]*?\*/", "", raw)
        raw_clean = re.sub(r"--[^\n]*", "", raw_clean)
        stmts = re.findall(
            r"(CREATE[^;]+;|INSERT[^;]+;)", raw_clean, re.IGNORECASE | re.DOTALL
        )
        n_create = 0
        n_insert = 0
        cur = conn.cursor()
        try:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for s in stmts:
                s = s.strip()
                if not s:
                    continue
                # 判断是 CREATE TABLE 还是 INSERT
                tbl_match = _TABLE_NAME_RE.search(s)
                if tbl_match:
                    tbl_name = tbl_match.group(1)
                    if tbl_name not in missing_tables:
                        continue  # 表已存在，跳过 CREATE
                # INSERT 语句始终执行（IGNORE 保证幂等）
                # ★ Fix (2026-07-12): INSERT IGNORE 移至 CREATE 分支外，始终执行
                try:
                    cur.execute(s)
                    if tbl_match:
                        n_create += 1
                    else:
                        n_insert += 1
                except pymysql.err.OperationalError as e:
                    code, msg = e.args
                    if code not in (1050, 1061, 1062):
                        _log.info(f"[DB] schema 警告({code}): {msg[:100]}")
                except Exception as e:
                    _log.warning(
                        f"[DB] schema 执行异常({type(e).__name__}): {str(e)[:120]}"
                    )
            conn.commit()
            with guard("_init_schema: cur.execute('SET FOREIGN_KEY_CHECKS=", log=_log):
                cur.execute("SET FOREIGN_KEY_CHECKS=1")
        finally:
            try:
                cur.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)
        _log.info(
            "[DB] DDL 完成: %d CREATE + %d INSERT（缺失 %d 表，INSERT 始终执行）",
            n_create,
            n_insert,
            len(missing_tables),
        )

        # ── 3.5 ALTER TABLE 迁移（★ 07-05h 新增，2026-07-13 重构预检查）──
        # 执行前先查 information_schema 判断目标对象是否已存在，存在则跳过，
        # 避免对大表执行 ADD COLUMN 时锁表+重建导致超时（2013）。
        raw_full = re.sub(r"/\*[\s\S]*?\*/", "", raw)
        raw_full = re.sub(r"--[^\n]*", "", raw_full)
        alter_stmts = re.findall(
            r"((?:ALTER\s+TABLE|DROP\s+TABLE)[^;]+;)",
            raw_full,
            re.IGNORECASE | re.DOTALL,
        )
        if alter_stmts:
            n_alter = 0
            n_skip = 0
            cur = conn.cursor()
            try:
                # 获取当前数据库名（供 information_schema 查询使用）
                cur.execute("SELECT DATABASE()")
                _db_row = cur.fetchone()
                _db_name = (_db_row or {}).get("DATABASE()") or None

                for s in alter_stmts:
                    s = s.strip()
                    if not s:
                        continue
                    # 解析 ALTER 语句，判断是否可预检查跳过
                    _should_skip = False
                    if _db_name:
                        _should_skip = _alter_already_applied(cur, _db_name, s)
                    if _should_skip:
                        n_skip += 1
                        continue
                    try:
                        cur.execute(s)
                        n_alter += 1
                    except pymysql.err.OperationalError as e:
                        code, msg = e.args
                        # 1060 = Duplicate column name（ADD COLUMN 幂等跳过）
                        # 1091 = Can't DROP COLUMN（DROP COLUMN 幂等跳过）
                        if code not in (1060, 1091):
                            _log.info(f"[DB] ALTER 警告({code}): {msg[:100]}")
                    except Exception as e:
                        _log.warning(
                            f"[DB] ALTER 执行异常({type(e).__name__}): {str(e)[:120]}"
                        )
                conn.commit()
            finally:
                try:
                    cur.close()
                except Exception as _e:
                    _log.debug("cur.close 失败: %s", _e, exc_info=True)
            _log.info(
                "[DB] ALTER 迁移预检查: 跳过 %d 条已应用, 执行 %d 条",
                n_skip,
                n_alter,
            )

        # ★ seed（无论表是否存在，每次启动都执行）
        with guard("_init_schema: from src.models.permission import _s", log=_log, level=logging.WARNING):
            from src.models.permission import _seed_default_permissions

            _seed_default_permissions(conn)
        _schema_done = True


def reset_schema_done() -> None:
    """重置 schema 初始化标志（供 db_manager.close_all 使用）。"""
    global _schema_done
    _schema_done = False
