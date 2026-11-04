# -*- coding: utf-8 -*-
"""
可视化报告 — 图表数据 API
=========================
提供 /api/report/chart-data 端点，返回多维度结构化统计数据，
供前端 ECharts 图表渲染使用。
"""

from datetime import datetime
from src.core.guarded import guard
from pathlib import Path
from flask import Blueprint, jsonify
import logging

_log = logging.getLogger(__name__)

# ── 阈值常量 ──
_HISTORY_MAX_VERSIONS = 10

from src.backend.state import _current_project
from src.models.permission import has_project_permission

chart_bp = Blueprint("chart_data", __name__)


@chart_bp.route("/api/report/chart-data")
def api_chart_data():
    """返回可视化报告所需的全部多维度统计数据。"""
    try:
        # ★ v2 权限：项目级 docs.view（admin/member 皆可查看本项目业务 KPI）
        from src.core.config_loader import get_current_user
        from src.users.manager import is_admin

        uid = get_current_user()
        if not uid:
            return jsonify({"ok": False, "error": "请先登录"}), 401
        proj = _current_project()
        if not proj:
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        if not is_admin(uid) and not has_project_permission(uid, proj, "docs.view"):
            return (
                jsonify(
                    {"ok": False, "error": "无权查看此项目图表（需要项目级 docs.view）"}
                ),
                403,
            )

        from src.pipeline.utils.case_store import case_store
        from src.core.config_loader import config as _cfg
        from src.users.manager import get_execution_status_map

        intents = case_store.list_intents_with_meta()
        # 只统计正式 Validation_Intent（冒烟测试用例独立于 KPI/图表口径）
        intents = [c for c in intents if _looks_like_test_case(c)]
        if not intents:
            return (
                jsonify({"ok": False, "error": "无测试用例数据，请先执行 Step 3"}),
                404,
            )

        # ★ 性能修复：限制只读 active 版本数据，避免拉取所有历史版本造成大表扫描
        from src.core.versioning import current_version_id

        active_vid = current_version_id(proj)

        # ★ 读取执行状态
        cids = [c.get("case_id", "") for c in intents if c.get("case_id")]
        status_map = get_execution_status_map(cids, proj)

        # ★ 合并状态到 intent
        for c in intents:
            c["verification_status"] = status_map.get(c.get("case_id", ""), "")

        # ── 1. 执行概览 ──
        total = len(intents)
        passed = sum(
            1 for c in intents if c.get("verification_status") in ("PASS", "FLAKY")
        )
        failed = sum(1 for c in intents if c.get("verification_status") == "FAIL")
        flaky = sum(1 for c in intents if c.get("verification_status") == "FLAKY")
        skipped = sum(1 for c in intents if c.get("verification_status") == "SKIP")
        pending = total - passed - failed - skipped
        executed = total - pending
        pass_rate = (
            round(passed / (passed + failed) * 100, 1) if (passed + failed) > 0 else 0.0
        )

        overview = {
            "total": total,
            "executed": executed,
            "passed": passed,
            "failed": failed,
            "flaky": flaky,
            "skipped": skipped,
            "pending": pending,
            "pass_rate": pass_rate,
            "flaky_rate": round(flaky / max(total, 1) * 100, 1),
        }

        # ── 2. 模块分布 ──
        # 构建用例 → 功能模块 映射
        # 数据链路：test_cases.req_id → requirements.req_id → requirements.slice_id → requirement_slices.feature_label
        # 但 requirements.slice_id 可能为空（旧数据），需从 payload.module（"切片req_raw:0"）反提 slice_id
        slice_feature_map = {}  # slice_id → feature_label
        slice_section_map = {}  # slice_id → section
        req_slice_map = {}  # req_id → slice_id（从 requirements 表桥接）
        try:
            from src.core.db_manager import get_connection, release_connection

            _conn = get_connection()
            try:
                # 1) 从 requirement_slices 获取 feature_label 和 section
                # ★ 性能修复：加 version_id 过滤，避免拉取所有历史版本
                if active_vid:
                    _rows = _conn.execute(
                        "SELECT slice_id, feature_label, section FROM requirement_slices WHERE project=%s AND version_id=%s",
                        (proj, active_vid),
                    ).fetchall()
                else:
                    _rows = _conn.execute(
                        "SELECT slice_id, feature_label, section FROM requirement_slices WHERE project=%s",
                        (proj,),
                    ).fetchall()
                for _row in _rows:
                    _sid = _row.get("slice_id", "")
                    if _sid:
                        slice_feature_map[_sid] = _row.get("feature_label", "") or ""
                        slice_section_map[_sid] = _row.get("section", "") or ""
                # 2) 从 requirements 表获取 req_id → slice_id 桥接
                # ★ 性能修复：加 version_id 过滤，避免拉取所有历史版本
                if active_vid:
                    _rows = _conn.execute(
                        "SELECT req_id, slice_id FROM requirements WHERE project=%s AND version_id=%s",
                        (proj, active_vid),
                    ).fetchall()
                else:
                    _rows = _conn.execute(
                        "SELECT req_id, slice_id FROM requirements WHERE project=%s",
                        (proj,),
                    ).fetchall()
            finally:
                # ★ 修复：release_connection 必须在 finally 块，避免异常时连接泄漏
                release_connection(_conn)
            for _row in _rows:
                _rid = _row.get("req_id", "")
                _sid = _row.get("slice_id", "") or ""
                if _rid and _sid:
                    req_slice_map[_rid] = _sid
        except Exception as _e:
            _log.debug("req_slice_map 构建失败: %s", _e, exc_info=True)

        modules = {}
        for c in intents:
            # 取功能模块名，优先级：feature_label → slice feature_label → slice section → module(非切片前缀) → 未分类
            m = c.get("feature_label", "") or ""
            if not m:
                # 尝试获取 slice_id：直接字段 → req_id 桥接 → 从 module 字段反提
                _sid = c.get("slice_id", "") or ""
                if not _sid:
                    _rid = c.get("req_id", "") or ""
                    if _rid:
                        _sid = req_slice_map.get(_rid, "") or ""
                if not _sid:
                    # 从 payload.module（"切片req_raw:0"）反提 slice_id
                    _raw_mod = c.get("module", "") or ""
                    if _raw_mod.startswith("切片"):
                        _sid = _raw_mod[2:]  # 去掉 "切片" 前缀
                if _sid:
                    m = slice_feature_map.get(_sid, "") or ""
                if not m and _sid:
                    m = slice_section_map.get(_sid, "") or ""
            if not m or m.startswith("切片"):
                _raw_mod = c.get("module", "") or ""
                if not _raw_mod.startswith("切片"):
                    m = _raw_mod
            if not m or m.startswith("切片"):
                m = "未分类"
            if m not in modules:
                modules[m] = {
                    "name": m,
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "pending": 0,
                }
            modules[m]["total"] += 1
            st = c.get("verification_status", "")
            if st == "PASS" or st == "FLAKY":
                modules[m]["passed"] += 1
            elif st == "FAIL":
                modules[m]["failed"] += 1
            else:
                modules[m]["pending"] += 1

        module_list = []
        for m, s in sorted(modules.items()):
            ex = s["total"] - s["pending"]
            module_list.append(
                {
                    "name": m,
                    "total": s["total"],
                    "passed": s["passed"],
                    "failed": s["failed"],
                    "pending": s["pending"],
                    "pass_rate": round(s["passed"] / ex * 100, 1) if ex > 0 else 0,
                }
            )

        # ── 3. 优先级分布 (P0-P4) ──
        risk_level = {}
        for c in intents:
            p = c.get("risk_level", "") or "P2"
            if p not in risk_level:
                risk_level[p] = {
                    "level": p,
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "pending": 0,
                }
            risk_level[p]["total"] += 1
            st = c.get("verification_status", "")
            if st == "PASS" or st == "FLAKY":
                risk_level[p]["passed"] += 1
            elif st == "FAIL":
                risk_level[p]["failed"] += 1
            else:
                risk_level[p]["pending"] += 1

        p_order = ["P0", "P1", "P2", "P3", "P4"]
        risk_level_list = []
        for p in p_order:
            if p in risk_level:
                s = risk_level.pop(p)
                ex = s["total"] - s["pending"]
                risk_level_list.append(
                    {
                        "level": p,
                        "total": s["total"],
                        "passed": s["passed"],
                        "failed": s["failed"],
                        "pending": s["pending"],
                        "pass_rate": round(s["passed"] / ex * 100, 1) if ex > 0 else 0,
                    }
                )
        # 剩余其他等级
        for p, s in sorted(risk_level.items()):
            ex = s["total"] - s["pending"]
            risk_level_list.append(
                {
                    "level": p,
                    "total": s["total"],
                    "passed": s["passed"],
                    "failed": s["failed"],
                    "pending": s["pending"],
                    "pass_rate": round(s["passed"] / ex * 100, 1) if ex > 0 else 0,
                }
            )

        # ── 4. 测试用例类型分布 ──
        case_types = {}
        for c in intents:
            t = c.get("case_type", "") or "未分类"
            if t not in case_types:
                case_types[t] = {
                    "type": t,
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "pending": 0,
                }
            case_types[t]["total"] += 1
            st = c.get("verification_status", "")
            if st == "PASS" or st == "FLAKY":
                case_types[t]["passed"] += 1
            elif st == "FAIL":
                case_types[t]["failed"] += 1
            else:
                case_types[t]["pending"] += 1

        type_list = []
        for t, s in sorted(case_types.items()):
            ex = s["total"] - s["pending"]
            type_list.append(
                {
                    "type": t,
                    "total": s["total"],
                    "passed": s["passed"],
                    "failed": s["failed"],
                    "pending": s["pending"],
                    "pass_rate": round(s["passed"] / ex * 100, 1) if ex > 0 else 0,
                }
            )

        # ── 5. 执行人分布 ──
        executors = {}
        for c in intents:
            u = c.get("author", "") or "unknown"
            if u not in executors:
                executors[u] = {
                    "name": u,
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "pending": 0,
                }
            executors[u]["total"] += 1
            st = c.get("verification_status", "")
            if st == "PASS" or st == "FLAKY":
                executors[u]["passed"] += 1
            elif st == "FAIL":
                executors[u]["failed"] += 1
            else:
                executors[u]["pending"] += 1

        executor_list = []
        for u, s in sorted(executors.items()):
            ex = s["total"] - s["pending"]
            executor_list.append(
                {
                    "name": u,
                    "total": s["total"],
                    "passed": s["passed"],
                    "failed": s["failed"],
                    "pending": s["pending"],
                    "pass_rate": round(s["passed"] / ex * 100, 1) if ex > 0 else 0,
                }
            )

        # ── 6. 历史版本趋势（从 MySQL 中的 case_asset_file + updated_at 聚合） ──
        history = []
        with guard("api_chart_data: seen = {}", log=_log, level=logging.WARNING):
            seen = {}
            for c in intents:
                cf = str(c.get("case_asset_file") or "").strip()
                if not cf:
                    continue
                ts = c.get("updated_at") or c.get("created_at") or ""
                entry = seen.setdefault(cf, {"name": cf, "time": "", "size": 0})
                if ts:
                    try:
                        entry["time"] = max(entry["time"], ts) if entry["time"] else ts
                    except Exception as e:
                        _log.warning("[chart-data] 时间比较失败: %s", e)
                        entry["time"] = ts
            versions = sorted(
                (v for v in seen.values() if v["name"].lower().startswith("test_case")),
                key=lambda v: v.get("time") or "",
            )
            for v in versions[-_HISTORY_MAX_VERSIONS:]:
                label = v.get("time", "")
                history.append(
                    {
                        "version": Path(v["name"]).stem.replace(
                            "Validation_Intent_", ""
                        ),
                        "label": str(label),
                        "size_kb": round(int(v.get("size") or 0) / 1024, 1),
                    }
                )

        # ── 7. 版本号 ──
        ver = _cfg.project_version or ""
        display_name = proj
        if ver:
            display_name = "%s--%s" % (proj, ver)

        return jsonify(
            {
                "ok": True,
                "project": display_name,
                "version": ver,
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "overview": overview,
                "modules": module_list,
                "risk_level": risk_level_list,
                "case_types": type_list,
                "executors": executor_list,
                "history": history,
                "has_data": executed > 0,
                "source": "mysql",
            }
        )
    except Exception as e:
        _log.error("[api_chart_data] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


def _looks_like_test_case(c):
    """仅当测试用例来源于正式 Validation_Intent（非 smoke 等独立测试用例）时纳入 KPI/图表统计。"""
    cf = str(c.get("case_asset_file") or "").lower()
    if cf.startswith("test_case"):
        return True
    # 兜底：没写 case_asset_file 时按 case_type 判断（旧数据兼容）
    ct = str(c.get("case_type") or "").lower()
    return "smoke" not in ct
