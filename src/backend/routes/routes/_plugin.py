# -*- coding: utf-8 -*-
"""
GET /api/plugin-metrics — Step4 插件指标

【拆分】从 data.py 提取，零业务逻辑变更。
【2026-06-22 重构】把"读 ai_health_metrics + 组装数据"提到模块顶层独立函数
collect_plugin_metrics(project)，view 包一层 jsonify+权限检查；指标 fetcher 直接复用前者。
"""

from flask import jsonify
import logging

_log = logging.getLogger(__name__)

from src.backend.routes._data_helpers import _current_project
from src.models.permission import check_admin_or_permission


def collect_plugin_metrics(project: str) -> dict:
    """采集 Step4 插件指标数据（不涉及 HTTP / 权限校验）。

    供两个入口共用：
    1. HTTP view  /api/plugin-metrics  → 包 jsonify 后返回
    2. metrics_page fetcher  → 直接取 dict 数据用于指标中心聚合

    返回:
        {
            "ok": True,
            "project": str,
            "ground_truth_total": int,
            "ground_truth_fail": int,
            "self_heal_total": int,
            "self_heal_success": int,
            "visual_baseline_status": str,
            "fcp_avg": float,
            "plugins": List[Any],
        }
        失败时返回 {"ok": False, "error": str}
    """
    try:
        from src.models.base import get_conn
        from src.backend.state import _current_version

        conn = get_conn()
        try:
            # 获取最近一次 run_id（按版本隔离）
            version_id = _current_version()
            if version_id:
                run_row = conn.execute(
                    "SELECT run_id FROM ai_health_metrics WHERE project=%s AND version_id=%s ORDER BY created_at DESC LIMIT 1",
                    (project, version_id),
                ).fetchone()
            else:
                run_row = conn.execute(
                    "SELECT run_id FROM ai_health_metrics WHERE project=%s ORDER BY created_at DESC LIMIT 1",
                    (project,),
                ).fetchone()
            run_id = run_row["run_id"] if run_row else None

            # Ground Truth 统计
            gt_total = 0
            gt_fail = 0
            if run_id:
                gt_rows = conn.execute(
                    "SELECT metric_name, SUM(value) as total_val FROM ai_health_metrics "
                    "WHERE project=%s AND run_id=%s AND metric_type='ground_truth' GROUP BY metric_name",
                    (project, run_id),
                ).fetchall()
                for r in gt_rows:
                    if r["metric_name"] == "gt_total":
                        gt_total = int(r["total_val"] or 0)
                    elif r["metric_name"] == "gt_fail":
                        gt_fail = int(r["total_val"] or 0)

            # 自愈合统计（含验证闭环）
            sh_total = 0
            sh_success = 0
            sh_verify_success = 0
            sh_verify_fail = 0
            if run_id:
                sh_rows = conn.execute(
                    "SELECT metric_name, SUM(value) as total_val FROM ai_health_metrics "
                    "WHERE project=%s AND run_id=%s AND metric_type='self_heal' GROUP BY metric_name",
                    (project, run_id),
                ).fetchall()
                for r in sh_rows:
                    if r["metric_name"] == "sh_total":
                        sh_total = int(r["total_val"] or 0)
                    elif r["metric_name"] == "sh_success":
                        sh_success = int(r["total_val"] or 0)
                    elif r["metric_name"] == "sh_verify_success":
                        sh_verify_success = int(r["total_val"] or 0)
                    elif r["metric_name"] == "sh_verify_fail":
                        sh_verify_fail = int(r["total_val"] or 0)

            # 视觉基线状态
            vb_status = "—"
            if run_id:
                vb_row = conn.execute(
                    "SELECT value FROM ai_health_metrics "
                    "WHERE project=%s AND run_id=%s AND metric_type='visual_baseline' "
                    "AND metric_name='snapshots' LIMIT 1",
                    (project, run_id),
                ).fetchone()
                if vb_row and vb_row["value"] is not None:
                    snap_count = int(vb_row["value"])
                    vb_status = "已建立" if snap_count > 0 else "—"

            # 性能指标 (FCP)
            fcp_avg = 0
            if run_id:
                fcp_row = conn.execute(
                    "SELECT AVG(value) as avg_val FROM ai_health_metrics "
                    "WHERE project=%s AND run_id=%s AND metric_type='fcp' AND metric_name='fcp_ms'",
                    (project, run_id),
                ).fetchone()
                if fcp_row and fcp_row["avg_val"] is not None:
                    fcp_avg = round(float(fcp_row["avg_val"]), 1)

            # 已注册插件列表（从 PluginManager 获取）
            from src.pipeline.steps.step4.plugins import PluginManager

            pm = PluginManager()
            plugins = pm.list_plugins() if hasattr(pm, "list_plugins") else []
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)
        return {
            "ok": True,
            "project": project,
            "ground_truth_total": gt_total,
            "ground_truth_fail": gt_fail,
            "self_heal_total": sh_total,
            "self_heal_success": sh_success,
            "self_heal_verify_success": sh_verify_success,  # ★ 验证闭环
            "self_heal_verify_fail": sh_verify_fail,  # ★ 验证闭环
            "visual_baseline_status": vb_status,
            "fcp_avg": fcp_avg,
            "plugins": plugins,
        }
    except Exception as e:
        _log.error("[collect_plugin_metrics] 异常: %s", e, exc_info=True)
        return {"ok": False, "error": "服务器内部错误"}


def register(bp):
    """将路由注册到 Blueprint。"""

    @bp.route("/api/plugin-metrics")
    def api_plugin_metrics():
        """★ v5: 从 ai_health_metrics 表读取 Step4 插件指标（不再硬编码 0）。"""
        try:
            ok, err = check_admin_or_permission(
                "execution.run", project=_current_project()
            )
            if not ok:
                return jsonify({"ok": False, "error": err}), (
                    401 if err.startswith("请先登录") else 403
                )
            project = _current_project()
            if not project:
                return jsonify({"ok": False, "error": "请先选择项目"}), 400

            data = collect_plugin_metrics(project)
            if not data.get("ok"):
                return jsonify(data), 500
            return jsonify(data)
        except Exception as e:
            _log.error("[api_plugin_metrics] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "服务器内部错误"}), 500
