# -*- coding: utf-8 -*-
"""
metrics_page 共享工具函数
"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


def _db_conn():
    """复用 health.py 的 DB 连接逻辑。"""
    try:
        from src.backend.routes.health import _get_conn

        return _get_conn()
    except Exception as e:
        _log.warning("[metrics] 无法获取 DB 连接: %s", e)
        return None


def _current_project_id():
    """获取当前项目名（供 ai_health_metrics 等 project 列过滤）。

    ★ 修复（2026-06-23）：原读 session.get('project')，但实际 session key 为
    'project_name'（见 project-session-recovery 修复），key 不匹配恒返回 None，
    导致 /api/metrics-summary 全域返回"需先选择项目"。改用 state._current_project()
    统一来源（已处理 session 恢复 + 全局变量 + config 兜底）。"""
    try:
        from src.backend.state import _current_project

        return _current_project() or None
    except Exception as _e:
        _log.debug("指标页当前项目获取失败: %s", _e, exc_info=True)
        return None


def _current_version_id() -> int:
    """获取当前版本数字ID（project_versions.id），供 metrics 查询版本隔离。

    ★ 修复（2026-08-14）：原返回 version_label 字符串，但 ai_health_metrics.version_id
    是 BIGINT 类型（存版本数字ID，默认0），字符串查 BIGINT 会被 MySQL 转成 0，
    导致版本隔离完全失效——所有数据都混在 version_id=0 下。
    现改为从 project_versions 表按 version_label 查询数字 ID，查不到返回 0。

    ★ 根因修复（2026-09-20）：会话未显式选择版本（_current_version 为空）时返回 0，
    但写端始终按 active version 落库 → 读端查 version_id=0 桶必然取不到数据
    （如 Step4 case_pass 在 executions 全迁到 active 版本后成 null）。现兜底为
    active version（request-context 无关），与写端对齐；仅当会话确实选了版本时才优先用它。
    """
    try:
        from src.backend.state import _current_version, _current_project

        version_label = _current_version() or ""
        project = _current_project() or ""
        if not project:
            return 0
        # 会话显式选定版本 → 优先按 label 解析数字 ID
        if version_label:
            conn = _db_conn()
            if conn:
                try:
                    row = conn.execute(
                        "SELECT id FROM project_versions "
                        "WHERE project=%s AND version_label=%s LIMIT 1",
                        (project, version_label),
                    ).fetchone()
                    if row and row["id"]:
                        return int(row["id"])
                finally:
                    with guard("_current_version_id: conn.close()", log=_log):
                        conn.close()
        # 未选版本 → 兜底 active version（与写端 record_metric 同源同构）
        with guard("_current_version_id: from src.core.versioning import curr", log=_log):
            from src.core.versioning import current_version_id

            _vid = current_version_id(project)
            if _vid:
                return int(_vid)
        return 0
    except Exception as _e:
        _log.debug("指标页当前版本ID获取失败: %s", _e, exc_info=True)
        return 0


def _num(v, default=None):
    """安全转为数字（优先保留小数，不截断浮点数）。"""
    if v is None or v == "":
        return default
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (ValueError, TypeError):
        try:
            return int(v)
        except (ValueError, TypeError):
            return default


def latest_run_id(conn, project, version_id=""):
    """查询最近一次 run_id（复用共享连接，避免各 fetcher 重复查询）。

    供 ai_overall / fusion / step4_plugins / p0_governance 等共享同一 run_id。
    返回 None 表示无数据或查询失败。
    """
    if not conn or not project:
        return None
    try:
        if version_id:
            row = conn.execute(
                "SELECT run_id FROM ai_health_metrics "
                "WHERE project=%s AND version_id=%s "
                "ORDER BY created_at DESC LIMIT 1",
                (project, version_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT run_id FROM ai_health_metrics "
                "WHERE project=%s "
                "ORDER BY created_at DESC LIMIT 1",
                (project,),
            ).fetchone()
        return row["run_id"] if row else None
    except Exception as e:
        _log.warning("[metrics] latest_run_id 查询异常: %s", e)
        return None


def latest_run_id_for_metrics(conn, project, version_id="", metric_types=None):
    """查询包含指定 metric_type 集合的最近一次 run_id。

    ★ 2026-07-13: 解决"全局最新 run 缺其他 step 指标"问题。
    不同 run 产出的指标子集差异巨大（如最新 run 只有 step0/3/4，缺 step2），
    按 created_at 取全局最新 run 会错过其他 run 里已有的指标数据。

    本函数按"有该指标的最新 run"查找，与 _step2_breakdown.py 的做法一致。
    仿照 _step2_breakdown 第 81-86 行的查询模式。

    ★ 2026-08-12: 增加版本 fallback —— 先按版本查，查不到再查全项目，
    避免因 version_id 不匹配导致整域指标全空。

    参数:
        conn: DB 连接
        project: 项目名
        version_id: 版本标签（空则不过滤版本）
        metric_types: list[str] — 关心的 metric_type 列表

    返回 run_id 或 None。
    """
    if not conn or not project or not metric_types:
        return None
    try:
        placeholders = ", ".join(["%s"] * len(metric_types))

        # ★ 查询优先级：版本级 → 项目级
        candidates = []
        if version_id:
            candidates.append(("version", version_id))
        candidates.append(("project", None))

        for scope, vid_candidate in candidates:
            if vid_candidate is not None:
                row = conn.execute(
                    f"SELECT run_id FROM ai_health_metrics "
                    f"WHERE project=%s AND version_id=%s AND metric_type IN ({placeholders}) "
                    f"ORDER BY created_at DESC LIMIT 1",
                    (project, vid_candidate) + tuple(metric_types),
                ).fetchone()
            else:
                row = conn.execute(
                    f"SELECT run_id FROM ai_health_metrics "
                    f"WHERE project=%s AND metric_type IN ({placeholders}) "
                    f"ORDER BY created_at DESC LIMIT 1",
                    (project,) + tuple(metric_types),
                ).fetchone()
            if row:
                return row["run_id"]

        return None
    except Exception as e:
        _log.warning("[metrics] latest_run_id_for_metrics 查询异常: %s", e)
        return None


def _ensure_run_index(conn):
    """确保 ai_health_metrics 有复合索引 idx_health_run_full（幂等）。

    每次调用检查 information_schema，仅在索引缺失时执行 ALTER TABLE，
    避免每次 API 请求都触发 DDL。覆盖已有数据库（schema.sql 仅对新库生效）。
    """
    if not conn:
        return
    with guard("_ensure_run_index: row = conn.execute( 'SELECT COUNT(*)", log=_log):
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() "
            "AND table_name = 'ai_health_metrics' "
            "AND index_name = 'idx_health_run_full'"
        ).fetchone()
        if row and int(row["cnt"] or 0) == 0:
            conn.execute(
                "ALTER TABLE ai_health_metrics "
                "ADD INDEX idx_health_run_full "
                "(project, run_id, step, metric_type, metric_name)"
            )
            _log.info("[metrics] 已添加复合索引 idx_health_run_full")
