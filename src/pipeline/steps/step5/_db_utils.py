# -*- coding: utf-8 -*-
"""
数据库工具函数
"""
from datetime import datetime
from src.core.guarded import guard
from typing import Any
import logging

_log = logging.getLogger(__name__)


def _ensure_run_summary_table(conn) -> None:
    """占位函数（2026-06-22：run_summary 表已统一由 src/core/schema.sql 负责）。"""
    return


def _current_run_id(default_prefix: str = "report") -> str:
    try:
        from src.backend.state import _thread_run_id

        rid = getattr(_thread_run_id, "value", "") or ""
        if rid:
            return str(rid)
    except Exception as _e:
        _log.debug("[_db_utils] _current_run_id 异常: %s", _e, exc_info=True)
    return "%s-%s" % (default_prefix, datetime.now().strftime("%Y%m%d%H%M%S"))


def _load_verification_status_from_db(intents, project_name):
    """从 MySQL 批量读取测试用例执行状态。返回 {case_id: status} 映射。"""
    try:
        from src.users.manager import get_execution_status_map

        cids = [c.get("case_id", "") for c in intents if c.get("case_id")]
        return get_execution_status_map(cids, project_name)
    except Exception as _e:
        _log.warning("  [报告] 加载执行状态失败: %s", _e)
        return {}


def _load_execution_confidence_from_db(intents, project_name):
    """★ v5.0: 从 executions 表批量读取用例 confidence，注入到 intents。

    根因: _collect_confidence_summary 依赖 intent["_confidence"]["confidence"]，
          但 intents 从 test_cases 表读取，无 _confidence 字段，导致 avg_confidence=0.0。
    通用性: 适用于所有项目，复用 get_execution_status_map 的查询模式（子查询取最新 executed_at）。
    职责边界: 仅在 Step5 报告生成时读取，不污染 Step4 原始记录。

    返回: {case_id: confidence_value(float)}
    """
    result = {}
    cids = [str(c.get("case_id", "")) for c in intents if c.get("case_id")]
    if not cids:
        return result
    with guard("_load_execution_confidence_from_db: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
        from src.core.db_manager import get_connection
        from src.core.versioning import current_version_id

        version_id = current_version_id(project_name) or 0
        conn = None
        try:
            conn = get_connection()
            # 分批查询（每批 500，防 IN 列表过长）
            for i in range(0, len(cids), 500):
                batch = cids[i : i + 500]
                ph = ",".join(["%s"] * len(batch))
                rows = (
                    conn.execute(
                        # ★ 通用口径修复(SKIP 不稀释置信度): 仅注入已真正验证(执行)的
                        #   PASS/FAIL 用例置信度。SKIP 未执行 verify(evidence_source=
                        #   fallback_no_verify)，其残余置信度(≈0.3)不该被平均进 avg_confidence。
                        #   过滤加在内外两侧，使 latest 取"最近一次已执行"而非"最近一次含跳过"。
                        "SELECT e.case_id, e.confidence FROM executions e "
                        "INNER JOIN (SELECT case_id, MAX(executed_at) AS max_at FROM executions "
                        "WHERE case_id IN ("
                        + ph
                        + ") AND project=%s AND version_id=%s "
                        "AND status IN ('PASS','FAIL') GROUP BY case_id) latest "
                        "ON e.case_id = latest.case_id AND e.executed_at = latest.max_at "
                        "WHERE e.project=%s AND e.version_id=%s "
                        "AND e.status IN ('PASS','FAIL')",
                        tuple(batch)
                        + (project_name, version_id, project_name, version_id),
                    ).fetchall()
                    or []
                )
                for row in rows:
                    cid = str(row.get("case_id", "") or "")
                    conf = row.get("confidence")
                    if cid and conf is not None:
                        try:
                            result[cid] = float(conf)
                        except (ValueError, TypeError) as e:
                            _log.warning("confidence转换失败: %s", e)
        finally:
            if conn:
                try:
                    conn.close()
                except (OSError, ConnectionError) as _e:
                    _log.warning("[_db_utils] conn.close 失败: %s", _e)
    return result


def _read_report_intents(excel_handler):
    """报告测试用例读取：使用 MySQL test_cases + case_meta（带证据字段）。

    ★ 根因修复(2026-09-20): 原用 list_intents() 仅查 test_cases，不含 case_meta 的
      证据字段（_evidence_confidence/_evidence_verdict/_evidence_cold_start 等），
      导致 Step5 汇总 evidence_calibrated_confidence / evidence_coverage 读到的全是
      None/0（case_meta 数据真实存在却取不到）。
      修复: 改用 list_intents_with_meta()（LEFT JOIN case_meta），与审核面板/KPI
      同口径取数，消除"同一证据字段两套读取路径"的缝合怪。
      通用性: 适用于所有项目，不依赖业务逻辑；list_intents_with_meta 的 5 秒
      TTL 缓存不影响数据新鲜性（报告生成前 Step4 已落库）。
    """
    from src.pipeline.utils.case_store import case_store

    intents = case_store.list_intents_with_meta()
    _log.info("  Step5报告读取: MySQL test_cases+case_meta (%d 条)", len(intents))
    return intents, "mysql"


def _read_report_requirements(excel_handler):
    """报告需求读取：仅使用 MySQL requirements。"""
    from src.pipeline.utils.requirement_store import requirement_store

    reqs = requirement_store.list_requirements()
    _log.info("  Step5需求读取: MySQL requirements (%d 条)", len(reqs))
    return reqs, "mysql"


def _load_source_quotes_for_cases(
    project_name: str, case_ids: list[str]
) -> dict[str, str]:
    """★ S45-015: 批量查询 case_id → source_quote 映射。

    查询链路: case_requirement_mapping(case_id→req_id) → requirements.payload_json[source_quote]

    参数:
        project_name: 项目名
        case_ids:     失败用例 ID 列表

    返回:
        {case_id: source_quote_text} 映射；查询失败或无数据返回空 dict。
    """
    if not case_ids:
        return {}
    try:
        from src.core.db_manager import get_connection
        from src.core.versioning import current_version_id
        import json as _json

        version_id = current_version_id(project_name) or 0
        # 占位符构造（防 SQL 注入：参数化 IN 列表）
        _placeholders = ",".join(["%s"] * len(case_ids))
        conn = None
        try:
            conn = get_connection()
            # 1) 批量查 case_id → req_id（%%s 转义防止 Python % 格式化误解析）
            rows = (
                conn.execute(
                    "SELECT case_id, req_id FROM case_requirement_mapping "
                    "WHERE project=%%s AND version_id=%%s AND case_id IN (%s)"
                    % _placeholders,
                    tuple([project_name, version_id] + list(case_ids)),
                ).fetchall()
                or []
            )
            if not rows:
                return {}
            case_to_req = {
                str(r.get("case_id", "")): str(r.get("req_id", ""))
                for r in rows
                if r.get("req_id")
            }
            if not case_to_req:
                return {}
            # 2) 批量查 req_id → payload_json
            req_ids = list(set(case_to_req.values()))
            _req_placeholders = ",".join(["%s"] * len(req_ids))
            req_rows = (
                conn.execute(
                    "SELECT req_id, payload_json FROM requirements "
                    "WHERE project=%%s AND version_id=%%s AND req_id IN (%s)"
                    % _req_placeholders,
                    tuple([project_name, version_id] + req_ids),
                ).fetchall()
                or []
            )
            req_to_quote = {}
            for r in req_rows:
                rid = str(r.get("req_id", ""))
                payload = r.get("payload_json")
                if not payload:
                    continue
                try:
                    payload_dict = (
                        _json.loads(payload) if isinstance(payload, str) else payload
                    )
                    sq = str(payload_dict.get("source_quote", "") or "").strip()
                    if sq:
                        req_to_quote[rid] = sq
                except (ValueError, TypeError) as _e:
                    _log.debug("[step5_db] payload 解析失败跳过(§5): %s", _e)
                    continue
            # 3) 组装 case_id → source_quote
            result = {}
            for cid, rid in case_to_req.items():
                if rid in req_to_quote:
                    result[cid] = req_to_quote[rid]
            _log.info(
                "  [S45-015] 失败用例原文追溯: %d/%d 命中", len(result), len(case_ids)
            )
            return result
        finally:
            if conn:
                try:
                    conn.close()
                except (OSError, ConnectionError) as _e:
                    _log.warning("[_db_utils] conn.close 失败: %s", _e)
    except Exception as _e:
        _log.warning("  [S45-015] 加载失败用例原文追溯失败: %s", _e)
        return {}


def _verify_report_stats_against_db(
    project_name: str,
    run_id: str,
    report_stats: dict[str, Any],
) -> dict[str, Any]:
    """★ S45-014: 报告数据自校验。

    从 executions 表按 run_id 聚合 COUNT(status)，与报告统计数对比。

    参数:
        project_name: 项目名
        run_id:       流水线运行ID
        report_stats: 报告统计 {total, passed, failed, skipped, pending}

    返回:
        {
          "verified": bool,        # 是否一致
          "db_counts": {...},      # DB 实际聚合
          "mismatches": [...],     # 不一致项列表
        }
        查询失败返回 {"verified": True, "skipped": True}（向后兼容）。
    """
    try:
        from src.core.db_manager import get_connection

        if not run_id:
            return {"verified": True, "skipped": True, "reason": "无 run_id"}
        conn = None
        try:
            conn = get_connection()
            rows = (
                conn.execute(
                    "SELECT status, COUNT(*) AS cnt FROM executions "
                    "WHERE project=%s AND run_id=%s GROUP BY status",
                    (project_name, run_id),
                ).fetchall()
                or []
            )
            if not rows:
                return {"verified": True, "skipped": True, "reason": "无执行记录"}
            db_counts = {
                str(r.get("status", "")): int(r.get("cnt", 0) or 0) for r in rows
            }
            mismatches = []
            _rep = report_stats or {}
            # 对比关键指标（DB 是按执行记录聚合，报告是按 case 聚合，可能有微小差异，仅警告）
            db_passed = db_counts.get("PASS", 0)
            db_failed = db_counts.get("FAIL", 0)
            db_skipped = db_counts.get("SKIP", 0)
            rep_passed = int(_rep.get("passed", 0) or 0)
            rep_failed = int(_rep.get("failed", 0) or 0)
            rep_skipped = int(_rep.get("skipped", 0) or 0)
            if db_passed != rep_passed:
                mismatches.append("passed: 报告=%d vs DB=%d" % (rep_passed, db_passed))
            if db_failed != rep_failed:
                mismatches.append("failed: 报告=%d vs DB=%d" % (rep_failed, db_failed))
            if db_skipped != rep_skipped:
                mismatches.append(
                    "skipped: 报告=%d vs DB=%d" % (rep_skipped, db_skipped)
                )
            verified = len(mismatches) == 0
            _log.info(
                "  [S45-014] 报告自校验: verified=%s, db_counts=%s", verified, db_counts
            )
            return {
                "verified": verified,
                "db_counts": db_counts,
                "mismatches": mismatches,
            }
        finally:
            if conn:
                try:
                    conn.close()
                except (OSError, ConnectionError) as _e:
                    _log.warning("[_db_utils] conn.close 失败: %s", _e)
    except Exception as _e:
        _log.warning("  [S45-014] 报告自校验失败: %s", _e)
        return {"verified": True, "skipped": True, "reason": str(_e)[:120]}
