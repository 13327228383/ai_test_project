# -*- coding: utf-8 -*-
"""
需求审核路由处理函数。
────────────────────────────────
api_requirements, api_review_requirement, api_confirm_all_requirements
"""

import time
from src.core.guarded import guard
import logging
from flask import jsonify, request

from src.models.permission import check_admin_or_permission
from src.backend.state import _current_project

_log = logging.getLogger(__name__)


def api_requirements():
    """GET /api/requirements — 获取需求列表。"""
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    if not _current_project():
        return jsonify({"ok": False, "error": "请先选择项目"}), 400
    try:
        from src.core.excel_handler import excel_handler
        from src.pipeline.utils.confidence_scorer import (
            score_requirement,
            confidence_color,
            get_avg_confidence,
        )

        reqs = excel_handler.read_requirements()
        # ★ 查询每个需求关联的测试用例数量
        req_case_counts = {}
        with guard("api_requirements: from src.pipeline.utils.case_store i", log=_log):
            from src.pipeline.utils.case_store import case_store

            all_intents = case_store.list_intents_with_meta()
            for intent in all_intents:
                rid = (intent.get("req_id") or "").strip()
                if rid:
                    req_case_counts[rid] = req_case_counts.get(rid, 0) + 1
        items = [
            {
                "req_id": r.get("req_id", ""),
                "module": r.get("module", ""),
                "description": r.get("description", ""),
                "operation": r.get("operation", ""),
                "condition_expr": r.get("condition_expr", ""),
                "expected_feedback": r.get("expected_feedback", ""),
                "source_quote": r.get("source_quote", ""),
                "source_anchor": r.get("source_anchor", ""),
                "slice_id": r.get("slice_id", ""),
                "is_implicit": r.get("is_implicit", 0),
                "dependency_req_id": r.get("dependency_req_id", ""),
                "is_generated": r.get("is_generated", False),
                "review_status": r.get("review_status", "pending"),
                "confidence": score_requirement(r),
                "confidence_color": confidence_color(
                    get_avg_confidence(score_requirement(r))
                ),
                "source": r.get("source", ""),
                # ★ 完整性检查 issues（不阻断拆解，供需求方审核参考）
                "completeness_issues": r.get("completeness_issues", []),
                # ★ 关联测试用例数量
                "_case_count": req_case_counts.get(r.get("req_id", ""), 0),
            }
            for r in reqs
        ]
        total = len(items)
        approved = sum(1 for i in items if i["review_status"] == "approved")
        # ★ 完整性问题统计
        with_issues = sum(1 for i in items if i["completeness_issues"])
        issue_count = sum(len(i["completeness_issues"]) for i in items)
        stats = {
            "total": total,
            "approved": approved,
            "pending": total - approved,
            "approval_rate": round(approved / max(total, 1) * 100, 1),
            "completeness_with_issues": with_issues,
            "completeness_issue_count": issue_count,
        }
        return jsonify({"requirements": items, "total": total, "stats": stats})
    except Exception as e:
        _log.error(f"[ERROR] requirements: {e}")
        return jsonify({"ok": False, "error": "服务器内部错误"})


def api_review_requirement():
    """POST /api/requirements/review — 审核单条需求（批准/修改/驳回/删除/新增）。"""
    ok, err = check_admin_or_permission("docs.edit", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    if not _current_project():
        return jsonify({"ok": False, "error": "请先选择项目"}), 400
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    req_id = data.get("req_id", "")
    try:
        from src.core.excel_handler import excel_handler

        if action == "approve":
            ok = excel_handler.update_review_status(req_id, "approved")
            return jsonify({"ok": ok})
        elif action == "modify":
            updates = data.get("data", {})
            ok = excel_handler.update_requirement(req_id, updates)
            excel_handler.update_review_status(req_id, "modified")
            return jsonify({"ok": ok})
        elif action == "reject":
            # ★ 驳回：需求不成立/退回需求方修改（不删除，保留驳回状态）
            #   与 delete 区别：delete 是删除条目；reject 是标记为"已驳回"保留记录
            comment = data.get("comment", "")
            ok = excel_handler.update_review_status(req_id, "rejected")
            # 记录驳回意见到 review_log
            with guard("api_review_requirement: from src.core.config_loader import g", log=_log, level=logging.WARNING):
                from src.core.config_loader import get_current_user
                from src.pipeline.utils.review_store import review_store

                reviewer = get_current_user() or "unknown"
                review_store.add_review_log(
                    item_type="requirement",
                    item_id=req_id,
                    action="reject",
                    reviewer=reviewer,
                    comment=comment,
                )
            return jsonify({"ok": ok})
        elif action == "delete":
            ok = excel_handler.delete_requirement(req_id)
            return jsonify({"ok": ok})
        elif action == "add":
            new_data = data.get("data", {})
            ok = excel_handler.add_requirement(new_data)
            return jsonify({"ok": ok})
        else:
            return jsonify({"ok": False, "error": "未知操作: %s" % action}), 400
    except Exception as e:
        _log.error(f"[ERROR] requirements/review: {e}")
        return jsonify({"ok": False, "error": "服务器内部错误"})


def api_confirm_all_requirements():
    """POST /api/requirements/review/confirm — 三车道分流批量确认（★ P1-7: 仅自动放行 green 车道，yellow/red 需逐条人审）。"""
    ok, err = check_admin_or_permission("docs.edit", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    if not _current_project():
        return jsonify({"ok": False, "error": "请先选择项目"}), 400
    try:
        from src.core.excel_handler import excel_handler
        from src.pipeline.utils.review_lane_store import read_req_lanes

        reqs = excel_handler.read_requirements()
        total = len(reqs)
        if total == 0:
            return (
                jsonify(
                    {"ok": False, "error": "无需求可确认，请先执行 Step2 拆解需求"}
                ),
                400,
            )

        # ★ P1-7: 读取三车道路由数据
        project = _current_project()
        lanes = read_req_lanes(project) if project else {}
        # lanes: {req_id: {lane, reason, routed_to_human}}

        # ★ P1-7: 闸门保护 — review_lane 无数据时拒绝批量
        if not lanes:
            _log.warning(
                "[P1-7] 批量确认拒绝: review_lane 无数据 (project=%s)", project
            )
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "未启用三车道分流（review_lane 表无数据），禁止一键全过。请重跑 Step2 生成分流，或逐条审核需求",
                        "code": "NO_LANE_DATA",
                    }
                ),
                400,
            )

        count = 0
        already_approved = 0  # ★ 已审核(approved/modified/rejected)的需求，如实上报供前端区分
        skipped_yellow = 0
        skipped_red = 0
        failed_req_ids = []
        for r in reqs:
            if r.get("review_status", "pending") not in (
                "approved",
                "modified",
                "rejected",
            ):
                req_id = r.get("req_id", "")
                lane_info = lanes.get(req_id, {})
                lane = lane_info.get(
                    "lane", "yellow"
                )  # 无 lane 数据默认 yellow（保守）
                if lane == "green":
                    if not excel_handler.update_review_status(req_id, "approved"):
                        failed_req_ids.append(req_id)
                        _log.warning(
                            "[requirements/review/confirm] 审核状态更新失败: %s", req_id
                        )
                    else:
                        count += 1
                elif lane == "yellow":
                    skipped_yellow += 1
                elif lane == "red":
                    skipped_red += 1
            else:
                already_approved += 1

        if failed_req_ids:
            _log.error(
                "[requirements/review/confirm] %d 条需求审核状态更新失败: %s",
                len(failed_req_ids),
                ",".join(failed_req_ids[:10]),
            )
        # ★ 记录审核日志
        with guard("api_confirm_all_requirements: from src.backend.routes.case_review ", log=_log, level=logging.WARNING):
            from src.backend.routes.case_review import _log_review

            _log_review(project, "requirement", "batch", "confirm_lane")
        # ★ 审核完成：解除暂停，恢复流水线继续执行 Step3
        with guard("api_confirm_all_requirements: from src.backend.state import _set_p", log=_log, level=logging.WARNING):
            from src.backend.state import _set_paused_step, _get_paused_step
            from src.pipeline.utils.pause_controller import pause_controller

            paused_step = _get_paused_step(project)
            _set_paused_step(0, project)
            pause_controller.resume()
            if paused_step > 0:
                with guard("api_confirm_all_requirements: import uuid, threading", log=_log, level=logging.WARNING):
                    import uuid, threading
                    from src.backend.routes.execution import (
                        _active_runs,
                        _active_runs_lock,
                        _make_session_runner,
                    )
                    from src.backend.executor._run_step import _run_step, StepRunOptions

                    run_id = uuid.uuid4().hex[:12]
                    with _active_runs_lock:
                        _active_runs[run_id] = {
                            "thread": None,
                            "force_stop": False,
                            "step": paused_step,
                            "project": project,
                            "start_ts": time.time(),
                        }
                    opts = StepRunOptions(
                        step_num=paused_step,
                        run_id=run_id,
                        step4_options={},
                        end_step=5,
                        resume=True,
                    )
                    t = threading.Thread(
                        target=_make_session_runner(_run_step, opts), daemon=True
                    )
                    with _active_runs_lock:
                        _active_runs[run_id]["thread"] = t
                    t.start()
                    _log.info(
                        "[审核闸门] 需求审核确认完成（green=%d yellow=%d red=%d），从 Step %d 恢复执行 (run_id=%s)",
                        count,
                        skipped_yellow,
                        skipped_red,
                        paused_step,
                        run_id,
                    )
            else:
                _log.info(
                    "[审核闸门] 需求审核确认完成（green=%d yellow=%d red=%d），流水线已恢复",
                    count,
                    skipped_yellow,
                    skipped_red,
                )
        return jsonify(
            {
                "ok": True,
                "approved": count,
                "skipped_yellow": skipped_yellow,
                "skipped_red": skipped_red,
                "already_approved": already_approved,
                "total": total,
            }
        )
    except Exception as e:
        _log.error(f"[ERROR] requirements/review/confirm: {e}")
        return jsonify({"ok": False, "error": "服务器内部错误"})
