# -*- coding: utf-8 -*-
"""
蓝红对抗指标获取器 (Blue-Red Adversarial Metrics)
==================================================
从 requirements / test_cases 表提取蓝红对抗流程的 KPI 数据。

★ 性能优化（2026-07-13）:
  - 原 Python 端循环解析 7000 行 JSON → 改用 MySQL JSON_EXTRACT 条件聚合
  - 单条 SQL 完成全部统计，零 Python 循环
  - 支持共享 conn，减少连接开销

Step2 指标:
  - 共识级别分布 (HIGHEST/HIGH/MEDIUM/LOW/SUPPLEMENTARY)
  - 仲裁判决分布 (approved/revised/rejected/supplementary)
  - 视角覆盖统计 (1/2/3 视角覆盖的需求数)

Step3 指标:
  - 蓝队三视角生成统计 (functional/security/integration)
  - 红队评审结果 (aggregate_score, overall_verdict)
  - 仲裁质量评分

★ 零硬编码：所有聚合逻辑纯数据驱动。
"""

import logging

_log = logging.getLogger(__name__)


def _current_project_id() -> str:
    from src.backend.state import _current_project

    return _current_project() or ""


def _current_version_id() -> str:
    from src.backend.state import _current_version

    return _current_version() or ""


def _db_conn():
    try:
        from src.models.base import get_conn

        return get_conn()
    except Exception as _e:
        _log.debug("蓝红指标 DB 连接获取失败: %s", _e, exc_info=True)
        return None


def _num(v, default=0):
    try:
        return int(v) if v is not None else default
    except Exception:
        return default


def _fetch_step2_blue_red(project: str, conn=None) -> dict:
    """Step 2 蓝红对抗指标：共识级别 + 仲裁判决分布。

    ★ JSON 下推：用 MySQL JSON_EXTRACT + 条件聚合，单条 SQL 完成全部统计。
    """
    result = {
        "consensus": {
            "HIGHEST": 0,
            "HIGH": 0,
            "MEDIUM": 0,
            "LOW": 0,
            "SUPPLEMENTARY": 0,
        },
        "verdicts": {"approved": 0, "revised": 0, "rejected": 0, "supplementary": 0},
        "perspective_coverage": {
            "1_perspective": 0,
            "2_perspectives": 0,
            "3_perspectives": 0,
        },
        "total_requirements": 0,
        "has_data": False,
    }

    _owns_conn = conn is None
    if _owns_conn:
        conn = _db_conn()
    if not conn:
        return result

    try:
        version_id = _current_version_id()
        # ★ 单条 SQL 完成全部 JSON 字段条件聚合，零 Python 循环
        if version_id:
            row = conn.execute(
                """
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.consensus_level')) = 'HIGHEST' THEN 1 ELSE 0 END) AS c_highest,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.consensus_level')) = 'HIGH' THEN 1 ELSE 0 END) AS c_high,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.consensus_level')) = 'MEDIUM' THEN 1 ELSE 0 END) AS c_medium,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.consensus_level')) = 'LOW' THEN 1 ELSE 0 END) AS c_low,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.consensus_level')) = 'SUPPLEMENTARY' THEN 1 ELSE 0 END) AS c_supp,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.verdict')) = 'approved' THEN 1 ELSE 0 END) AS v_approved,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.verdict')) = 'revised' THEN 1 ELSE 0 END) AS v_revised,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.verdict')) = 'rejected' THEN 1 ELSE 0 END) AS v_rejected,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.verdict')) = 'supplementary' THEN 1 ELSE 0 END) AS v_supp,
                  SUM(CASE WHEN CAST(JSON_EXTRACT(payload_json, '$.perspective_count') AS SIGNED) >= 3 THEN 1 ELSE 0 END) AS pc_3,
                  SUM(CASE WHEN CAST(JSON_EXTRACT(payload_json, '$.perspective_count') AS SIGNED) = 2 THEN 1 ELSE 0 END) AS pc_2,
                  SUM(CASE WHEN CAST(JSON_EXTRACT(payload_json, '$.perspective_count') AS SIGNED) = 1 THEN 1 ELSE 0 END) AS pc_1
                FROM requirements
                WHERE project=%s AND version_id=%s
                  AND payload_json IS NOT NULL AND payload_json != '{}'
                """,
                (project, version_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.consensus_level')) = 'HIGHEST' THEN 1 ELSE 0 END) AS c_highest,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.consensus_level')) = 'HIGH' THEN 1 ELSE 0 END) AS c_high,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.consensus_level')) = 'MEDIUM' THEN 1 ELSE 0 END) AS c_medium,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.consensus_level')) = 'LOW' THEN 1 ELSE 0 END) AS c_low,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.consensus_level')) = 'SUPPLEMENTARY' THEN 1 ELSE 0 END) AS c_supp,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.verdict')) = 'approved' THEN 1 ELSE 0 END) AS v_approved,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.verdict')) = 'revised' THEN 1 ELSE 0 END) AS v_revised,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.verdict')) = 'rejected' THEN 1 ELSE 0 END) AS v_rejected,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.verdict')) = 'supplementary' THEN 1 ELSE 0 END) AS v_supp,
                  SUM(CASE WHEN CAST(JSON_EXTRACT(payload_json, '$.perspective_count') AS SIGNED) >= 3 THEN 1 ELSE 0 END) AS pc_3,
                  SUM(CASE WHEN CAST(JSON_EXTRACT(payload_json, '$.perspective_count') AS SIGNED) = 2 THEN 1 ELSE 0 END) AS pc_2,
                  SUM(CASE WHEN CAST(JSON_EXTRACT(payload_json, '$.perspective_count') AS SIGNED) = 1 THEN 1 ELSE 0 END) AS pc_1
                FROM requirements
                WHERE project=%s AND version_id=0
                  AND payload_json IS NOT NULL AND payload_json != '{}'
                """,
                (project,),
            ).fetchone()

        if not row or _num(row["total"], 0) == 0:
            return result

        result["has_data"] = True
        result["total_requirements"] = _num(row["total"], 0)
        result["consensus"]["HIGHEST"] = _num(row["c_highest"], 0)
        result["consensus"]["HIGH"] = _num(row["c_high"], 0)
        result["consensus"]["MEDIUM"] = _num(row["c_medium"], 0)
        result["consensus"]["LOW"] = _num(row["c_low"], 0)
        result["consensus"]["SUPPLEMENTARY"] = _num(row["c_supp"], 0)
        result["verdicts"]["approved"] = _num(row["v_approved"], 0)
        result["verdicts"]["revised"] = _num(row["v_revised"], 0)
        result["verdicts"]["rejected"] = _num(row["v_rejected"], 0)
        result["verdicts"]["supplementary"] = _num(row["v_supp"], 0)
        result["perspective_coverage"]["3_perspectives"] = _num(row["pc_3"], 0)
        result["perspective_coverage"]["2_perspectives"] = _num(row["pc_2"], 0)
        result["perspective_coverage"]["1_perspective"] = _num(row["pc_1"], 0)

    except Exception as e:
        _log.warning("[blue_red] Step2 指标查询异常: %s", e)
    finally:
        if _owns_conn:
            try:
                conn.close()
            except Exception as e:
                _log.debug("[blue_red] 关闭连接失败: %s", e)

    return result


def _fetch_step3_blue_red(project: str, conn=None) -> dict:
    """Step 3 蓝红对抗指标：从 test_cases.payload_json 提取蓝队/红队/仲裁统计。

    ★ JSON 下推：用 MySQL JSON_EXTRACT + 条件聚合 + AVG，单条 SQL 完成全部统计。
    """
    result = {
        "blue_team": {"functional": 0, "security": 0, "integration": 0, "total": 0},
        "red_team_pass_rate": None,
        "arbiter_quality_score": None,
        "total_cases": 0,
        "has_data": False,
    }

    _owns_conn = conn is None
    if _owns_conn:
        conn = _db_conn()
    if not conn:
        return result

    try:
        version_id = _current_version_id()
        # ★ 单条 SQL 完成全部统计：COUNT + SUM + AVG，零 Python 循环
        if version_id:
            row = conn.execute(
                """
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$._source_perspective')) = 'functional' THEN 1 ELSE 0 END) AS b_func,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$._source_perspective')) = 'security' THEN 1 ELSE 0 END) AS b_sec,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$._source_perspective')) = 'integration' THEN 1 ELSE 0 END) AS b_int,
                  AVG(JSON_EXTRACT(payload_json, '$._red_team_report.aggregate_score')) AS avg_red,
                  AVG(JSON_EXTRACT(payload_json, '$.overall_quality_score')) AS avg_arbiter
                FROM test_cases
                WHERE project=%s AND version_id=%s
                  AND payload_json IS NOT NULL AND payload_json != '{}'
                """,
                (project, version_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$._source_perspective')) = 'functional' THEN 1 ELSE 0 END) AS b_func,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$._source_perspective')) = 'security' THEN 1 ELSE 0 END) AS b_sec,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$._source_perspective')) = 'integration' THEN 1 ELSE 0 END) AS b_int,
                  AVG(JSON_EXTRACT(payload_json, '$._red_team_report.aggregate_score')) AS avg_red,
                  AVG(JSON_EXTRACT(payload_json, '$.overall_quality_score')) AS avg_arbiter
                FROM test_cases
                WHERE project=%s AND version_id=0
                  AND payload_json IS NOT NULL AND payload_json != '{}'
                """,
                (project,),
            ).fetchone()

        if not row or _num(row["total"], 0) == 0:
            return result

        result["has_data"] = True
        result["total_cases"] = _num(row["total"], 0)
        result["blue_team"]["functional"] = _num(row["b_func"], 0)
        result["blue_team"]["security"] = _num(row["b_sec"], 0)
        result["blue_team"]["integration"] = _num(row["b_int"], 0)
        result["blue_team"]["total"] = (
            result["blue_team"]["functional"]
            + result["blue_team"]["security"]
            + result["blue_team"]["integration"]
        )

        # AVG 自动忽略 NULL，结果非 NULL 时才有意义
        if row["avg_red"] is not None:
            try:
                result["red_team_pass_rate"] = round(float(row["avg_red"]), 3)
            except (TypeError, ValueError) as _e:
                _log.debug(
                    "[blue_red] red_team_pass_rate 解析异常: %s", _e, exc_info=True
                )
        if row["avg_arbiter"] is not None:
            try:
                result["arbiter_quality_score"] = round(float(row["avg_arbiter"]), 3)
            except (TypeError, ValueError) as _e:
                _log.debug(
                    "[blue_red] arbiter_quality_score 解析异常: %s", _e, exc_info=True
                )

    except Exception as e:
        _log.warning("[blue_red] Step3 指标查询异常: %s", e)
    finally:
        if _owns_conn:
            try:
                conn.close()
            except Exception as e:
                _log.debug("[blue_red] 关闭连接失败: %s", e)

    return result


def fetch_blue_red_metrics(conn=None) -> dict:
    """聚合 Step2 + Step3 蓝红对抗 KPI。

    可选传入共享 conn，传入则复用不关闭；未传入则各开各关。

    返回: {step2: {...}, step3: {...}, has_data: bool}
    """
    project = _current_project_id()
    if not project:
        return {"step2": None, "step3": None, "has_data": False, "note": "需先选择项目"}

    step2 = _fetch_step2_blue_red(project, conn=conn)
    step3 = _fetch_step3_blue_red(project, conn=conn)

    return {
        "step2": step2,
        "step3": step3,
        "has_data": step2.get("has_data") or step3.get("has_data"),
    }
