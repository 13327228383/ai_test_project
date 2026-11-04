# -*- coding: utf-8 -*-
"""KPI 聚合器（KpiAggregator）—— 流水线强化专项 T5。

★ 职责：跨 MySQL 多表（run_summary / executions / coverage_summaries /
  coverage_results / fake_pass_audit）+ JSONL 台账（aggregate_ledger_summary）
  单次聚合统一指标字典，每项指标附 `source` 标注（可追溯至数据源）。

★ 指标字典（design §7.5 唯一事实源）：
  total / passed / failed / flaky / skipped / blocked /
  pass_rate / coverage_rate / fake_pass_rate / ledger_mismatch / closure_rate /
  avg_confidence / low_confidence_count （≥13 项，含 source 追溯）。

★ 容错：单个数据源异常 → 该指标降级为 {"value": 0/None, "source": "...", "error": ...}，
  整体仍返回 HTTP 200（绝不因单点失败导致整页报错，对齐 quality_bp 先例）。
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any

_log = logging.getLogger(__name__)


def _metric(value: Any, source: str, error: str = "") -> dict[str, Any]:
    """构造带 source 标注的指标项。"""
    out: dict[str, Any] = {"value": value, "source": source}
    if error:
        out["error"] = error
    return out


def _ledger_summary(target: str | None) -> dict[str, Any]:
    """懒加载台账聚合（含可选依赖 pydantic 的 import）。

    ★ 根因（2026-08-26）：原在模块顶层 ``from src.verification.aggregate import
      aggregate_ledger_summary``，会把 ledger 数据源的可选依赖（pydantic）拖进整个
      聚合器的 import 链——任一可选依赖缺失时，KPI 聚合器（含 total/pass_rate/blocked
      等与 ledger 无关的指标）整体 import 失败，违背本模块「单数据源异常仅该指标降级、
      整体仍可用」的容错契约。改为函数内懒加载 + 异常保护，ledger 缺失时返回空 summary，
      其余指标照常聚合。
    """
    try:
        from src.verification.aggregate import aggregate_ledger_summary

        return aggregate_ledger_summary(target)
    except Exception as e:  # noqa: BLE001
        _log.warning("[KpiAggregator] ledger 聚合加载失败: %s", str(e)[:160])
        return {}


class KpiAggregator:
    """统一 KPI 聚合器（单次聚合 + 趋势 + 分布 + 台账不符明细）。"""

    def __init__(self) -> None:
        """初始化（无状态；DB 连接按需获取）。"""

    # ─────────────────────────────────────────────────────────────────
    # 公开 API
    # ─────────────────────────────────────────────────────────────────
    def aggregate(
        self,
        project: str,
        run_id: str = "",
        version_id: str = "",
        target: str = "",
    ) -> dict[str, Any]:
        """单次聚合全部指标。

        Args:
            project: 项目名。
            run_id: 流水线运行 ID（空 = 最新一次运行）。
            version_id: 项目版本 ID。
            target: 台账靶子 key（空 = 聚合全部靶子台账）。

        Returns:
            {"ok": True, "project": ..., "run_id": ...,
             "metrics": {...13 项指标...}, "distribution": {...},
             "trend": [...], "ledger_mismatches": [...]}
        """
        # ★ 版本隔离修复(2026-08-30)：原实现取"项目最新一次运行"(忽略版本)，切换版本后
        #   会残留旧版本 KPI。现将随后所有"DEFAULT最新"查询收敛到当前活跃版本内。
        active_vid = self._resolve_version_id(project, version_id)
        run_id = run_id or self._latest_run_id(project, active_vid)
        metrics: dict[str, dict[str, Any]] = {}
        # ★ 权威优先级修正（2026-08-29 根因）：executions 是执行明细的唯一事实源，
        #   run_summary 仅是可选的预聚合缓存——但原实现只要存在 run_summary 行就优先
        #   信任它，导致"数据被清理/重跑后 run_summary 陈旧"时，KPI 面板与 executions
        #   /dashboard 对不上（实测 api_demo_sut 应 100% PASS 却显示 0%）。故这里先取
        #   run_summary，随后用 _counts_metrics（实时聚合 executions）覆盖计数类指标；
        #   仅当 executions 无记录时，_counts_metrics 返回 total=0 + 比率类 None（暂无数据），
        #   两者皆优先于陈旧的 run_summary 预聚合值。
        metrics.update(self._from_run_summary(project, run_id, active_vid))
        metrics.update(self._counts_metrics(project, run_id, active_vid))
        metrics.update(self._from_executions(project, run_id, active_vid))
        metrics.update(self._from_coverage(project, active_vid, run_id))
        metrics.update(self._from_fake_pass(project, run_id, active_vid))
        metrics.update(self._from_ledger(target or None))
        distribution = self._execution_distribution(project, run_id, active_vid)
        trend = self.trend(project, n=10, version_id=active_vid)
        ledger_mismatches = _ledger_summary(target or None).get(
            "mismatches", []
        )
        return {
            "ok": True,
            "project": project,
            "run_id": run_id,
            "metrics": metrics,
            "distribution": distribution,
            "trend": trend,
            "ledger_mismatches": ledger_mismatches,
        }

    def trend(
        self, project: str, n: int = 10, version_id: str = ""
    ) -> list[dict[str, Any]]:
        """近 N 次运行的 KPI 趋势（run_summary 按时间倒序取 N 条，再正序返回）。

        Args:
            project: 项目名。
            n: 趋势窗口（design 待明确②默认 10）。
            version_id: 版本 ID；传入时仅展示该版本的趋势，杜绝跨版本残留。

        Returns:
            [{run_id, created_at, pass_rate, coverage_rate, fake_pass_rate, total}, ...]
        """
        vid = self._to_int(version_id)
        n = max(1, min(int(n or 10), 50))
        rows: list[dict[str, Any]] = []
        with guard("trend: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
            from src.core.db_manager import get_connection

            conn = None
            try:
                conn = get_connection()
                if vid:
                    raw = conn.execute(
                        "SELECT run_id, total_cases, total_passed, total_failed, "
                        "total_flaky, total_skipped, pass_rate, avg_confidence, created_at "
                        "FROM run_summary WHERE project=%s AND version_id=%s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (project, str(vid), n),
                    ).fetchall() or []
                else:
                    raw = conn.execute(
                        "SELECT run_id, total_cases, total_passed, total_failed, "
                        "total_flaky, total_skipped, pass_rate, avg_confidence, created_at "
                        "FROM run_summary WHERE project=%s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (project, n),
                    ).fetchall() or []
                if not raw:
                    # ★ 读侧闭环兜底：工程流水线常只落 executions、跳过 Step5 报告持久化，
                    #   run_summary 为空时趋势会一直空白。此处从 executions 按 run_id 分组
                    #   实时聚合出趋势点，口径与 _counts_metrics 一致（防漂移），零写库风险。
                    raw = conn.execute(
                        "SELECT run_id AS run_id,"
                        " MAX(executed_at) AS created_at,"
                        " COUNT(*) AS total_cases,"
                        " SUM(status IN ('PASS','PASS_WITH_SLOW_BUG')) AS total_passed,"
                        " SUM(status IN ('FAIL','ERROR')) AS total_failed,"
                        " SUM(status='FLAKY') AS total_flaky,"
                        " SUM(status='SKIP') AS total_skipped,"
                        " CASE WHEN COUNT(*) - SUM(status IN ('SKIP','BLOCKED','PRE_GATE_BLOCKED')) <= 0"
                        "      THEN 0"
                        "      ELSE ROUND(SUM(status IN ('PASS','PASS_WITH_SLOW_BUG')) / "
                        "           (COUNT(*) - SUM(status IN ('SKIP','BLOCKED','PRE_GATE_BLOCKED'))), 4)"
                        " END AS pass_rate,"
                        " AVG(CASE WHEN status IN ('PASS','PASS_WITH_SLOW_BUG','FAIL')"
                        "       THEN confidence END) AS avg_confidence"
                        " FROM executions WHERE project=%s"
                        " AND case_id NOT LIKE 'pre_gate_%%' AND status != 'PRE_GATE'"
                        " GROUP BY run_id ORDER BY created_at DESC LIMIT %s",
                        (project, n),
                    ).fetchall() or []
                    # ★ 版本隔离(2026-08-30)：executions 兜底趋势也仅归当前活跃版本
                    if vid and raw:
                        raw = conn.execute(
                            "SELECT run_id AS run_id,"
                            " MAX(executed_at) AS created_at,"
                            " COUNT(*) AS total_cases,"
                            " SUM(status IN ('PASS','PASS_WITH_SLOW_BUG')) AS total_passed,"
                            " SUM(status IN ('FAIL','ERROR')) AS total_failed,"
                            " SUM(status='FLAKY') AS total_flaky,"
                            " SUM(status='SKIP') AS total_skipped,"
                            " CASE WHEN COUNT(*) - SUM(status IN ('SKIP','BLOCKED','PRE_GATE_BLOCKED')) <= 0"
                            "      THEN 0"
                            "      ELSE ROUND(SUM(status IN ('PASS','PASS_WITH_SLOW_BUG')) / "
                            "           (COUNT(*) - SUM(status IN ('SKIP','BLOCKED','PRE_GATE_BLOCKED'))), 4)"
                            " END AS pass_rate,"
                            " AVG(CASE WHEN status IN ('PASS','PASS_WITH_SLOW_BUG','FAIL')"
                            "       THEN confidence END) AS avg_confidence"
                            " FROM executions WHERE project=%s AND version_id=%s"
                            " AND case_id NOT LIKE 'pre_gate_%%' AND status != 'PRE_GATE'"
                            " GROUP BY run_id ORDER BY created_at DESC LIMIT %s",
                            (project, vid, n),
                        ).fetchall() or []
                for r in reversed(raw):
                    rows.append(
                        {
                            "run_id": r.get("run_id", ""),
                            "created_at": str(r.get("created_at") or ""),
                            "total": int(r.get("total_cases") or 0),
                            "passed": int(r.get("total_passed") or 0),
                            "failed": int(r.get("total_failed") or 0),
                            "flaky": int(r.get("total_flaky") or 0),
                            "skipped": int(r.get("total_skipped") or 0),
                            "pass_rate": float(r.get("pass_rate") or 0),
                            "avg_confidence": float(r.get("avg_confidence") or 0),
                        }
                    )
            finally:
                if conn:
                    with guard("trend: conn.close()", log=_log):
                        conn.close()
        return rows

    # ─────────────────────────────────────────────────────────────────
    # 各数据源采集（每项独立容错）
    # ─────────────────────────────────────────────────────────────────
    def _from_run_summary(
        self, project: str, run_id: str, version_id: str = ""
    ) -> dict[str, dict]:
        """run_summary：total/passed/failed/flaky/skipped/pass_rate/avg_confidence/low_confidence_count。"""
        out: dict[str, dict] = {}
        vid = self._to_int(version_id)

        def _defaults() -> dict[str, dict]:
            return {
                "total": _metric(0, "run_summary"),
                "passed": _metric(0, "run_summary"),
                "failed": _metric(0, "run_summary"),
                "flaky": _metric(0, "run_summary"),
                "skipped": _metric(0, "run_summary"),
                "pass_rate": _metric(0.0, "run_summary"),
                "avg_confidence": _metric(0.0, "run_summary"),
                "low_confidence_count": _metric(0, "run_summary"),
            }

        try:
            from src.core.db_manager import get_connection

            conn = None
            try:
                conn = get_connection()
                if run_id:
                    row = conn.execute(
                        "SELECT * FROM run_summary WHERE project=%s AND run_id=%s "
                        "ORDER BY id DESC LIMIT 1",
                        (project, run_id),
                    ).fetchone()
                elif vid:
                    row = conn.execute(
                        "SELECT * FROM run_summary WHERE project=%s AND version_id=%s "
                        "ORDER BY id DESC LIMIT 1",
                        (project, str(vid)),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT * FROM run_summary WHERE project=%s "
                        "ORDER BY id DESC LIMIT 1",
                        (project,),
                    ).fetchone()
                if row:
                    out["total"] = _metric(int(row.get("total_cases") or 0), "run_summary")
                    out["passed"] = _metric(int(row.get("total_passed") or 0), "run_summary")
                    out["failed"] = _metric(int(row.get("total_failed") or 0), "run_summary")
                    out["flaky"] = _metric(int(row.get("total_flaky") or 0), "run_summary")
                    out["skipped"] = _metric(int(row.get("total_skipped") or 0), "run_summary")
                    out["pass_rate"] = _metric(round(float(row.get("pass_rate") or 0), 4), "run_summary")
                    out["avg_confidence"] = _metric(round(float(row.get("avg_confidence") or 0), 4), "run_summary")
                    out["low_confidence_count"] = _metric(
                        int(row.get("low_confidence_count") or 0), "run_summary"
                    )
                else:
                    # 无 run_summary 记录 → 回退到 executions 真实明细汇总。
                    # 工程流水线（e2e_pipeline 等）常直接落 executions 而跳过 step5 报告
                    # 持久化，若聚合器只读 run_summary 会把真实结果算成 0。executions
                    # 才是执行明细的唯一事实源，run_summary 仅作可选的预聚合缓存。
                    out = self._counts_metrics(project, run_id, str(vid or ""))
            finally:
                if conn:
                    with guard("_from_run_summary: conn.close()", log=_log):
                        conn.close()
        except Exception as e:  # noqa: BLE001
            _log.warning("[KpiAggregator] run_summary 采集失败: %s", str(e)[:160])
            out = _defaults()
            out["total"] = _metric(None, "run_summary", str(e)[:120])
        return out

    def _from_executions(
        self, project: str, run_id: str, version_id: str = ""
    ) -> dict[str, dict]:
        """executions：blocked（BLOCKED/PRE_GATE_BLOCKED）+ total 回退。"""
        out: dict[str, dict] = {}
        vid = self._to_int(version_id)
        try:
            from src.core.db_manager import get_connection

            conn = None
            try:
                conn = get_connection()
                if run_id:
                    row = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM executions "
                        "WHERE project=%s AND run_id=%s AND status IN (%s,%s)",
                        (project, run_id, "BLOCKED", "PRE_GATE_BLOCKED"),
                    ).fetchone()
                elif vid:
                    row = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM executions "
                        "WHERE project=%s AND version_id=%s AND status IN (%s,%s)",
                        (project, vid, "BLOCKED", "PRE_GATE_BLOCKED"),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM executions "
                        "WHERE project=%s AND status IN (%s,%s)",
                        (project, "BLOCKED", "PRE_GATE_BLOCKED"),
                    ).fetchone()
                out["blocked"] = _metric(
                    int(row.get("cnt") or 0) if row else 0, "executions"
                )
            finally:
                if conn:
                    with guard("_from_executions: conn.close()", log=_log):
                        conn.close()
        except Exception as e:  # noqa: BLE001
            _log.warning("[KpiAggregator] executions 采集失败: %s", str(e)[:160])
            out["blocked"] = _metric(None, "executions", str(e)[:120])
        return out

    def _counts_metrics(
        self, project: str, run_id: str, version_id: str = ""
    ) -> dict[str, dict]:
        """executions 全量计数兜底：total/passed/failed/flaky/skipped/pass_rate/
        avg_confidence/low_confidence_count。

        events 状态归类口径（与 _execution_distribution 一致，防漂移）：
          passed = PASS / PASS_WITH_SLOW_BUG
          failed = FAIL / ERROR
          flaky  = FLAKY
          skipped = SKIP（仅真正跳过的用例）
          blocked = BLOCKED / PRE_GATE_BLOCKED（独立指标，见 _from_executions）
          PRE_GATE = 门禁告警占位（非阻塞），既不算 skipped 也不算 blocked；
                     且 case_id 形如 'pre_gate_stepN' 的门禁骨架记录不计入 total。
        pass_rate = passed / total_execution，total_execution = 真实执行的用例总数。
        """
        out: dict[str, dict] = {}
        defaults = {
            "total": _metric(0, "executions"),
            "passed": _metric(0, "executions"),
            "failed": _metric(0, "executions"),
            "flaky": _metric(0, "executions"),
            "skipped": _metric(0, "executions"),
            "pass_rate": _metric(0.0, "executions"),
            "avg_confidence": _metric(0.0, "executions"),
            "low_confidence_count": _metric(0, "executions"),
        }
        try:
            from src.core.db_manager import get_connection

            conn = None
            try:
                conn = get_connection()
                # ★ 流程层口径修正（2026-08-24）：门禁骨架记录（case_id=pre_gate_stepN，
                #   check_type=pre_gate）不是真实用例，不应计入 total；PRE_GATE 是门禁告警
                #   占位，归入 blocked 口径而非 skipped。skipped 仅统计真正跳过的 SKIP。
                sql = (
                    "SELECT COUNT(*) AS total,"
                    " SUM(status IN ('PASS','PASS_WITH_SLOW_BUG')) AS passed,"
                    " SUM(status IN ('FAIL','ERROR')) AS failed,"
                    " SUM(status='FLAKY') AS flaky,"
                    " SUM(status='SKIP') AS skipped,"
                    " SUM(status IN ('BLOCKED','PRE_GATE_BLOCKED')) AS blocked,"
                    " AVG(CASE WHEN status IN ('PASS','PASS_WITH_SLOW_BUG','FAIL')"
                    "       THEN confidence END) AS avg_confidence,"
                    " SUM(status IN ('PASS','PASS_WITH_SLOW_BUG') AND confidence IS NOT NULL"
                    "     AND confidence < 0.70) AS low_conf"
                    " FROM executions WHERE project=%s"
                    " AND case_id NOT LIKE 'pre_gate_%%'"
                    " AND status != 'PRE_GATE'"
                )
                args: list[Any] = [project]
                if run_id:
                    sql += " AND run_id=%s"
                    args.append(run_id)
                elif self._to_int(version_id):
                    # ★ 版本隔离(2026-08-30)：无 run_id 时按当前活跃版本过滤，防跨版本残留
                    sql += " AND version_id=%s"
                    args.append(self._to_int(version_id))
                row = conn.execute(sql, args).fetchone() or {}
                total = int(row.get("total") or 0)
                passed = int(row.get("passed") or 0)
                failed = int(row.get("failed") or 0)
                flaky = int(row.get("flaky") or 0)
                skipped = int(row.get("skipped") or 0)
                blocked = int(row.get("blocked") or 0)
                # ★ 口径对齐 report_generator/attribution：BLOCKED/PRE_GATE_BLOCKED 是阻断
                #   用例，既不算执行成功也不算 skipped，pass_rate 分母应剔除，避免 KPI 面板
                #   通过率被阻断用例拉低（与 Step5 报告层唯一正确口径保持一致，防漂移）。
                executable = total - skipped - blocked
                pass_rate = round(passed / executable, 4) if executable > 0 else 0.0
                avg_conf = row.get("avg_confidence")
                # 有真实执行明细 → 归 executions；完全无记录（total=0）→ 归 run_summary，
                # 与聚合器"无数据时以 run_summary 为默认口径"的历史契约保持一致。
                src = "executions" if total > 0 else "run_summary"
                out["total"] = _metric(total, src)
                out["passed"] = _metric(passed, src)
                out["failed"] = _metric(failed, src)
                out["flaky"] = _metric(flaky, src)
                out["skipped"] = _metric(skipped, src)
                if total <= 0:
                    # ★ 与覆盖率/假绿率口径对齐（2026-08-29）：无任何执行数据源时，
                    #   比率类指标显示「暂无数据」，而非假 0%——避免面板里
                    #   通过率/置信度=0%，覆盖率却=暂无数据 的自相矛盾。
                    out["pass_rate"] = _metric(None, src)
                    out["avg_confidence"] = _metric(None, src)
                else:
                    out["pass_rate"] = _metric(pass_rate, src)
                    out["avg_confidence"] = _metric(
                        round(float(avg_conf or 0), 4), src
                    )
                out["low_confidence_count"] = _metric(
                    int(row.get("low_conf") or 0), src
                )
            finally:
                if conn:
                    with guard("_counts_metrics: conn.close()", log=_log):
                        conn.close()
        except Exception as e:  # noqa: BLE001
            _log.warning("[KpiAggregator] executions 计数采集失败: %s", str(e)[:160])
            out = defaults
        return out

    def _execution_distribution(
        self, project: str, run_id: str, version_id: str = ""
    ) -> dict[str, int]:
        """executions 状态分布（PASS/FAIL/FLAKY/SKIP/BLOCKED/PRE_GATE_BLOCKED/ERROR）。

        真实状态归并口径（与 _counts_metrics 一致，防漂移）：
          PASS_WITH_SLOW_BUG → PASS（有最终通过态）
          PRE_GATE           → 门禁告警占位（非阻塞），不归 SKIP 也不归 BLOCKED；
                               其骨架记录（case_id=pre_gate_stepN）不参与分布。
        """
        vid = self._to_int(version_id)
        dist: dict[str, int] = {
            "PASS": 0,
            "FAIL": 0,
            "FLAKY": 0,
            "SKIP": 0,
            "BLOCKED": 0,
            "PRE_GATE_BLOCKED": 0,
            "ERROR": 0,
        }
        status_map = {"PASS_WITH_SLOW_BUG": "PASS"}
        with guard("_execution_distribution: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
            from src.core.db_manager import get_connection

            conn = None
            try:
                conn = get_connection()
                if run_id:
                    rows = conn.execute(
                        "SELECT status, COUNT(*) AS cnt FROM executions "
                        "WHERE project=%s AND run_id=%s "
                        "AND case_id NOT LIKE 'pre_gate_%%' GROUP BY status",
                        (project, run_id),
                    ).fetchall() or []
                elif vid:
                    rows = conn.execute(
                        "SELECT status, COUNT(*) AS cnt FROM executions "
                        "WHERE project=%s AND version_id=%s "
                        "AND case_id NOT LIKE 'pre_gate_%%' GROUP BY status",
                        (project, vid),
                    ).fetchall() or []
                else:
                    rows = conn.execute(
                        "SELECT status, COUNT(*) AS cnt FROM executions "
                        "WHERE project=%s "
                        "AND case_id NOT LIKE 'pre_gate_%%' GROUP BY status",
                        (project,),
                    ).fetchall() or []
                for r in rows:
                    status = status_map.get(
                        str(r.get("status") or "").upper(),
                        str(r.get("status") or "").upper(),
                    )
                    if status in dist:
                        # 累加而非覆盖：多个原始状态可能映射到同一展示态（如 PRE_GATE、SKIP → SKIP）
                        dist[status] += int(r.get("cnt") or 0)
            finally:
                if conn:
                    with guard("_execution_distribution: conn.close()", log=_log):
                        conn.close()
        return dist

    def _from_coverage(
        self, project: str, version_id: str, run_id: str
    ) -> dict[str, dict]:
        """coverage_summaries：coverage_rate（缺省取最新一次运行）。"""
        out: dict[str, dict] = {}
        try:
            from src.core.db_manager import get_connection

            conn = None
            try:
                conn = get_connection()
                row = (
                    conn.execute(
                        "SELECT coverage_rate, total_cases FROM coverage_summaries "
                        "WHERE project=%s AND run_id=%s ORDER BY created_at DESC LIMIT 1",
                        (project, run_id),
                    ).fetchone()
                    if run_id
                    else None
                )
                if not row:
                    # ★ 覆盖摘要 run_id 可能与 run_summary/executions 命名不一致
                    #   （如 hospital 覆盖摘要用 20260829_004502，运行用 *_v1_0），
                    #   精确按 run_id 匹配不到时回退到该项目最新一条覆盖摘要，
                    #   避免"数据存在却误显示暂无数据"。通用兜底，非单项目补丁。
                    # ★ 版本隔离(2026-08-30)：回退查询按当前活跃版本过滤，防跨版本残留。
                    if self._to_int(version_id):
                        row = conn.execute(
                            "SELECT coverage_rate, total_cases FROM coverage_summaries "
                            "WHERE project=%s AND version_id=%s ORDER BY created_at DESC LIMIT 1",
                            (project, self._to_int(version_id)),
                        ).fetchone()
                    if not row and not self._to_int(version_id):
                        row = conn.execute(
                            "SELECT coverage_rate, total_cases FROM coverage_summaries "
                            "WHERE project=%s ORDER BY created_at DESC LIMIT 1",
                            (project,),
                        ).fetchone()
                if row:
                    coverage_rate = row.get("coverage_rate")
                    total_cases = row.get("total_cases")
                    # ★ 区分"无数据源"和"真实 0"：total_cases=0/None 时无覆盖率数据
                    if total_cases is None or total_cases == 0:
                        out["coverage_rate"] = _metric(None, "coverage_summaries")
                    else:
                        out["coverage_rate"] = _metric(
                            round(float(coverage_rate or 0), 4),
                            "coverage_summaries",
                        )
                else:
                    # 无可统计数据源记录 → 返回 None（区别于"真实 0"），前端据此显示"暂无数据"
                    out["coverage_rate"] = _metric(None, "coverage_summaries")
            finally:
                if conn:
                    with guard("_from_coverage: conn.close()", log=_log):
                        conn.close()
        except Exception as e:  # noqa: BLE001
            _log.warning("[KpiAggregator] coverage 采集失败: %s", str(e)[:160])
            out["coverage_rate"] = _metric(None, "coverage_summaries", str(e)[:120])
        return out

    def _from_fake_pass(
        self, project: str, run_id: str, version_id: str = ""
    ) -> dict[str, dict]:
        """fake_pass_audit：fake_pass_rate（复用既有审计器，防口径漂移）。

        ★ 与覆盖率口径对齐（2026-08-29）：项目/运行无任何 executions 数据源时返回
        None → 前端显示「暂无数据」，而非固定 0%——消除"覆盖率暂无数据、假绿率却=0%"
        的指标来源矛盾。仅「真实执行 → 0 假绿」才返回 0。
        """
        out: dict[str, dict] = {}
        try:
            from src.pipeline.steps.step5.fake_pass_audit import audit_fake_passes

            if not self._execution_exists(project, run_id, version_id):
                out["fake_pass_rate"] = _metric(None, "fake_pass_audit")
                out["fake_pass_alert"] = _metric(None, "fake_pass_audit")
            else:
                report = audit_fake_passes(project, run_id)
                out["fake_pass_rate"] = _metric(
                    round(float(report.get("rate") or 0), 4), "fake_pass_audit"
                )
                out["fake_pass_alert"] = _metric(bool(report.get("alert")), "fake_pass_audit")
        except Exception as e:  # noqa: BLE001
            _log.warning("[KpiAggregator] fake_pass 采集失败: %s", str(e)[:160])
            out["fake_pass_rate"] = _metric(None, "fake_pass_audit", str(e)[:120])
        return out

    def _execution_exists(
        self, project: str, run_id: str, version_id: str = ""
    ) -> bool:
        """项目/运行是否存在 executions 数据源（区分「真实 0」与「无数据源」）。"""
        vid = self._to_int(version_id)
        try:
            from src.core.db_manager import get_connection

            conn = None
            try:
                conn = get_connection()
                if run_id:
                    row = conn.execute(
                        "SELECT 1 FROM executions WHERE project=%s AND run_id=%s LIMIT 1",
                        (project, run_id),
                    ).fetchone()
                elif vid:
                    row = conn.execute(
                        "SELECT 1 FROM executions WHERE project=%s AND version_id=%s LIMIT 1",
                        (project, vid),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT 1 FROM executions WHERE project=%s LIMIT 1",
                        (project,),
                    ).fetchone()
                return bool(row)
            finally:
                if conn:
                    with guard("_execution_exists: conn.close()", log=_log):
                        conn.close()
        except Exception as e:  # noqa: BLE001
            _log.warning("[KpiAggregator] executions 存在性检查失败: %s", str(e)[:160])
            return False

    def _from_ledger(self, target: str | None) -> dict[str, dict]:
        """JSONL 台账：ledger_mismatch / closure_rate（复用 aggregate_ledger_summary）。"""
        out: dict[str, dict] = {}
        try:
            summary = _ledger_summary(target)
            out["ledger_mismatch"] = _metric(
                int(summary.get("consistent_false") or 0), "ledger"
            )
            out["closure_rate"] = _metric(
                round(float(summary.get("closure_rate") or 0), 4), "ledger"
            )
            out["ledger_total"] = _metric(int(summary.get("total") or 0), "ledger")
            out["ledger_truth_unavailable"] = _metric(
                int(summary.get("truth_unavailable") or 0), "ledger"
            )
        except Exception as e:  # noqa: BLE001
            _log.warning("[KpiAggregator] ledger 聚合失败: %s", str(e)[:160])
            out["ledger_mismatch"] = _metric(None, "ledger", str(e)[:120])
            out["closure_rate"] = _metric(None, "ledger", str(e)[:120])
        return out

    # ─────────────────────────────────────────────────────────────────
    # 内部辅助
    # ─────────────────────────────────────────────────────────────────
    def _latest_run_id(self, project: str, version_id: str = "") -> str:
        """取某项目最近一次 run_id（run_summary 最新一条）。

        ★ 隔离修复(2026-08-30)：原实现忽略 version，切换版本后仍取到"项目最近一次"
        的 run_id，导致 KPI 面板在切换到空版本时残留旧版本数据。现限定在当前活跃版本内
        取最新 run_id；当前版本无运行时返回空串，由下游以版本过滤兜底（防跨版本残留）。
        """
        vid = self._to_int(version_id)
        try:
            from src.core.db_manager import get_connection

            conn = None
            try:
                conn = get_connection()
                if vid:
                    row = conn.execute(
                        "SELECT run_id FROM run_summary WHERE project=%s AND version_id=%s "
                        "ORDER BY id DESC LIMIT 1",
                        (project, str(vid)),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT run_id FROM run_summary WHERE project=%s "
                        "ORDER BY id DESC LIMIT 1",
                        (project,),
                    ).fetchone()
                return str(row.get("run_id") or "") if row else ""
            finally:
                if conn:
                    with guard("_latest_run_id: conn.close()", log=_log):
                        conn.close()
        except Exception as e:  # noqa: BLE001
            _log.warning("[KpiAggregator] 最新 run_id 查询失败: %s", str(e)[:160])
            return ""

    @staticmethod
    def _to_int(v) -> int | None:
        """版本 ID 安全转 int（run_summary.version_id 为 varchar、executions 为 bigint）。"""
        try:
            if v is None or v == "":
                return None
            return int(v)
        except Exception:
            return None

    def _resolve_version_id(self, project: str, version_id) -> str:
        """解析当前活跃版本 ID（供无 run_id 兜底查询做版本过滤）。

        ★ 隔离修复(2026-08-30)：调用方未显式传版本时，取当前活跃版本，使所有
        "该项目最新数据"的兜底查询都收敛到当前版本，杜绝跨版本残留。
        """
        if self._to_int(version_id):
            return str(self._to_int(version_id))
        try:
            from src.core.versioning import current_version_id

            v = current_version_id(project)
            return str(self._to_int(v) or "")
        except Exception:
            return ""
