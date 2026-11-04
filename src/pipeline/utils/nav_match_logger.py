# -*- coding: utf-8 -*-
"""导航匹配日志落库（v5.15, 2026-08-04）——L2 LLM 语义匹配可观测化。

用途：Step3 导航解析器每次需求→菜单匹配都记录一行（匹配级别/LLM 调用/耗时/结果），
供人工校准与失败率统计。表 navigation_match_logs：
  - match_level: L1(纯算法命中) / L2(LLM 语义命中) / L3(降级) / none
  - llm_used / llm_success / llm_elapsed_ms / llm_raw_response / llm_menu / menu_hit
  - calibration_status: pending / corrected（人工校准）

通用性：任何系统导航解析都可复用；写库失败静默（不阻塞建模）。
"""

import json
from src.core.guarded import guard
import logging
import time

_log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS navigation_match_logs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  project VARCHAR(64) NOT NULL DEFAULT '',
  version_id BIGINT NOT NULL DEFAULT 0 COMMENT '项目版本ID → project_versions.id，0=legacy',
  case_id VARCHAR(128) NOT NULL DEFAULT '',
  req_text VARCHAR(512) NOT NULL DEFAULT '',
  match_level VARCHAR(16) NOT NULL DEFAULT 'none',
  llm_used TINYINT NOT NULL DEFAULT 0,
  llm_elapsed_ms INT NOT NULL DEFAULT 0,
  llm_success TINYINT NOT NULL DEFAULT 0,
  llm_raw_response VARCHAR(512) NOT NULL DEFAULT '',
  llm_menu VARCHAR(128) NOT NULL DEFAULT '',
  menu_hit TINYINT NOT NULL DEFAULT 0,
  calibration_status VARCHAR(16) NOT NULL DEFAULT 'pending',
  corrected_menu VARCHAR(128) NOT NULL DEFAULT '',
  calibrated_by VARCHAR(64) NOT NULL DEFAULT '',
  calibrated_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_project (project),
  KEY idx_calibration (calibration_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_TABLE_CHECKED = False


def _get_conn():
    from src.core.db_manager import get_connection

    return get_connection()


def _active_version_id(project: str) -> int:
    """取当前活跃版本ID（0=legacy/无版本）。"""
    try:
        if not project:
            return 0
        from src.core.versioning import current_version_id

        return int(current_version_id(project) or 0)
    except Exception:
        return 0


def ensure_table():
    """幂等建表（每次写前检查一次，进程内只跑一遍），并对存量库补 version_id 列。"""
    global _TABLE_CHECKED
    if _TABLE_CHECKED:
        return
    with guard("ensure_table: conn = _get_conn()", log=_log, level=logging.WARNING):
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
                # ★ 存量库 ALTER：确保 version_id 列存在（对已建的旧表补列，不重建）
                with guard("ensure_table: cur.execute( 'ALTER TABLE navigation", log=_log):
                    cur.execute(
                        "ALTER TABLE navigation_match_logs "
                        "ADD COLUMN version_id BIGINT NOT NULL DEFAULT 0 "
                        "COMMENT '项目版本ID → project_versions.id，0=legacy'"
                    )
            conn.commit()
        finally:
            conn.close()
        _TABLE_CHECKED = True


def log_match(
    project: str,
    case_id: str,
    req_text: str,
    match_level: str,
    llm_used: bool = False,
    llm_elapsed_ms: int = 0,
    llm_success: bool = False,
    llm_raw: str = "",
    llm_menu: str = "",
    menu_hit: bool = False,
) -> None:
    """记录一次需求→菜单匹配（写库失败静默，不阻塞建模）。"""
    with guard("log_match: ensure_table()", log=_log):
        ensure_table()
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO navigation_match_logs(project, version_id, case_id, req_text, "
                    "match_level, llm_used, llm_elapsed_ms, llm_success, "
                    "llm_raw_response, llm_menu, menu_hit) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        project or "",
                        _active_version_id(project),
                        case_id or "",
                        (req_text or "")[:500],
                        match_level,
                        1 if llm_used else 0,
                        int(llm_elapsed_ms),
                        1 if llm_success else 0,
                        (llm_raw or "")[:500],
                        (llm_menu or "")[:120],
                        1 if menu_hit else 0,
                    ),
                )
            conn.commit()
        finally:
            conn.close()


def query_logs(
    project: str = "", level: str = "", calibration: str = "", limit: int = 100
) -> list:
    """查询导航匹配日志（供人工校准界面）。"""
    try:
        ensure_table()
        conn = _get_conn()
        try:
            sql = "SELECT * FROM navigation_match_logs WHERE 1=1"
            args = []
            if project:
                sql += " AND project=%s"
                args.append(project)
                # ★ 版本隔离：限定当前活跃版本，避免跨版本残留
                sql += " AND version_id=%s"
                args.append(_active_version_id(project))
            if level:
                sql += " AND match_level=%s"
                args.append(level)
            if calibration:
                sql += " AND calibration_status=%s"
                args.append(calibration)
            sql += " ORDER BY id DESC LIMIT %s"
            args.append(min(int(limit), 500))
            with conn.cursor() as cur:
                cur.execute(sql, args)
                # db_manager 连接为 DictCursor → 行已是 dict，直接返回
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        _log.warning("[NavMatchLog] 查询失败: %s", str(e)[:80])
        return []


def calibrate(log_id: int, corrected_menu: str, user: str = "") -> bool:
    """人工校准：确认或纠正某条匹配结果。"""
    try:
        ensure_table()
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE navigation_match_logs SET calibration_status='corrected', "
                    "corrected_menu=%s, calibrated_by=%s, calibrated_at=NOW() "
                    "WHERE id=%s",
                    ((corrected_menu or "")[:120], (user or "")[:64], log_id),
                )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:
        _log.warning("[NavMatchLog] 校准失败: %s", str(e)[:80])
        return False


def stats(project: str = "") -> dict:
    """匹配统计：L1/L2/L3 分布 + LLM 成功率（可观测核心）。"""
    try:
        ensure_table()
        conn = _get_conn()
        try:
            sql_where = " WHERE project=%s" if project else ""
            args = [project] if project else []
            if project:
                # ★ 版本隔离：统计限定当前活跃版本
                sql_where += " AND version_id=%s"
                args.append(_active_version_id(project))
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT match_level AS ml, COUNT(*) AS cnt "
                    f"FROM navigation_match_logs{sql_where} GROUP BY match_level",
                    args,
                )
                level_dist = {
                    (r.get("ml") or "none"): int(r.get("cnt") or 0)
                    for r in (cur.fetchall() or [])
                }
                cur.execute(
                    f"SELECT COUNT(*) AS c, SUM(llm_used) AS lu, SUM(llm_success) AS ls, "
                    f"AVG(llm_elapsed_ms) AS am FROM navigation_match_logs{sql_where}",
                    args,
                )
                agg = cur.fetchone() or {}
                total = agg.get("c") or 0
                llm_used = agg.get("lu") or 0
                llm_success = agg.get("ls") or 0
                avg_ms = agg.get("am") or 0
                # corrected 统计：WHERE 条件独立拼接（避免 AND 前无 WHERE）
                corr_sql = (
                    "SELECT COUNT(*) AS c FROM navigation_match_logs"
                    f"{sql_where} AND calibration_status='corrected'"
                    if sql_where
                    else "SELECT COUNT(*) AS c FROM navigation_match_logs "
                    "WHERE calibration_status='corrected'"
                )
                cur.execute(corr_sql, args if sql_where else [])
                corrected = (cur.fetchone() or {}).get("c") or 0
            return {
                "total": int(total or 0),
                "level_dist": {k: int(v) for k, v in (level_dist or {}).items()},
                "llm_used": int(llm_used or 0),
                "llm_success": int(llm_success or 0),
                # ★ 强制 float：pymysql SUM/AVG 返回 Decimal，Flask 默认 JSON 编码器无法
                #   序列化 Decimal，否则 /api/nav-match/stats 会 500 → 前端"加载失败"
                "llm_success_rate": (
                    float(round((llm_success / llm_used), 3)) if llm_used else 0.0
                ),
                "avg_llm_ms": float(round((avg_ms or 0), 1)),
                "corrected": int(corrected or 0),
            }
        finally:
            conn.close()
    except Exception as e:
        _log.warning("[NavMatchLog] 统计失败: %s", str(e)[:80])
        return {"total": 0, "error": str(e)[:80]}
