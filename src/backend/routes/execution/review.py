# -*- coding: utf-8 -*-
"""
执行控制 — 需求审核端点
========================
P1-6: 需求审核门控 (approve / reject)。
★ v2: 审核状态已迁移到 requirements.review_status DB 实体字段，
     不再使用内存 _review_state。每条需求/用例的审核状态持久化在 MySQL 中。
"""

import time
from src.core.guarded import guard
import threading
from flask import jsonify, request
import logging

_log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# ★ v2: DB-only 审核状态查询（替代原 _review_state 内存 dict）
#  审核状态直接读 requirements.review_status 字段，重启不丢失。
# ══════════════════════════════════════════════════════════


def _get_review_state():
    """★ DB-only：从 requirements 表汇总审核状态，供前端轮询。
    返回格式兼容旧 _review_state 调用方:
      {"status": "paused"|"approved", "pending": N, "total": N, "approval_rate": float}
    ★ 当 total==0 但流水线暂停在 Step2 时，返回 paused 状态（Step2 失败/空产出场景）
    ★ 修复：当 active_version_id=0 时，返回空数据（避免查询 legacy 数据）
    """
    try:
        from src.backend.state import _get_paused_step
        from src.core.excel_handler import excel_handler
        from src.core.versioning import current_version_id
        from src.core.config_loader import config

        project = config.project_name or ""
        active_version_id = current_version_id(project)

        # ★ 修复：version_id=0 时返回空数据（避免查询 legacy 数据）
        if active_version_id == 0:
            _log.debug("[审核状态] active_version_id=0，跳过查询避免 legacy 数据")
            return {}

        reqs = excel_handler.read_requirements()
        total = len(reqs)
        if total == 0:
            # ★ 边缘：无需求数据但流水线暂停在 Step2（拆解失败/提交前取消等）
            # ★ A1 改造：按 project 查询暂停状态
            from src.backend.state import _current_project

            if _get_paused_step(_current_project()) == 2:
                return {
                    "status": "paused",
                    "pending": 0,
                    "total": 0,
                    "approval_rate": 0,
                    "message": "需求拆解完成，等待审核...",
                }
            return {}
        pending = sum(
            1
            for r in reqs
            if r.get("review_status", "pending") not in ("approved", "modified")
        )
        approved = total - pending
        approval_rate = round(approved / max(total, 1) * 100, 1)
        if pending > 0:
            return {
                "status": "paused",
                "pending": pending,
                "total": total,
                "approval_rate": approval_rate,
            }
        else:
            return {
                "status": "approved",
                "approved": approved,
                "total": total,
                "approval_rate": approval_rate,
            }
    except Exception as e:
        _log.debug("[审核状态] DB 查询失败: %s", str(e)[:80])
        return {}


# ══════════════════════════════════════════════════════════
# 审核路由
# ══════════════════════════════════════════════════════════

# 模块级守卫：防止 .pyc 错乱或多重导入路径导致 endpoint 重复注册。
_ROUTES_REGISTERED = False


def _register_review_routes():
    """注册审核路由到 exec_bp。延迟导入避免循环依赖。"""
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return
    _ROUTES_REGISTERED = True

    from src.backend.routes.execution import exec_bp
    from src.backend.state import _set_paused_step

    @exec_bp.route("/api/review/approve", methods=["POST"])
    def api_review_approve():
        """★ DB-only：审核批准 — 批量确认所有待审核需求。"""
        from src.models.permission import check_admin_or_permission
        from src.backend.state import _current_project

        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )

        data = request.get_json(silent=True) or {}
        comment = data.get("comment", "")
        project = _current_project() or ""

        # ★ 批量确认所有 pending 需求
        try:
            from src.core.excel_handler import excel_handler

            reqs = excel_handler.read_requirements()
            approved = 0
            failed_req_ids = []
            for r in reqs:
                if r.get("review_status", "pending") not in ("approved", "modified"):
                    req_id = r.get("req_id", "")
                    if not excel_handler.update_review_status(req_id, "approved"):
                        failed_req_ids.append(req_id)
                        _log.warning("[review/approve] 审核状态更新失败: %s", req_id)
                    else:
                        approved += 1
            if failed_req_ids:
                _log.error(
                    "[review/approve] %d 条需求审核状态更新失败: %s",
                    len(failed_req_ids),
                    ",".join(failed_req_ids[:10]),
                )

            # ★ 记录审核日志
            with guard("api_review_approve: from src.backend.routes.case_review ", log=_log):
                from src.backend.routes.case_review import _log_review

                _log_review(project, "requirement", "batch", "approve", comment)

            # ★ 恢复流水线
            # ★ A1 改造：按 project 读取/清除暂停状态
            from src.backend.state import _get_paused_step, _current_project

            _cur_proj = _current_project()
            paused_step = _get_paused_step(_cur_proj)
            _set_paused_step(0, _cur_proj)

            if paused_step > 0:
                with guard("api_review_approve: import uuid", log=_log, level=logging.WARNING):
                    import uuid
                    from src.backend.routes.execution import (
                        _active_runs,
                        _active_runs_lock,
                        _make_session_runner,
                    )
                    from src.backend.executor._run_step import _run_step, StepRunOptions

                    # ★ 通用修复(2026-07-20 v3.7): 审核通过恢复流水线时从下一步开始，禁用 checkpoint 恢复
                    #   根因: 之前 resume=True 会从 checkpoint 恢复，但旧 run_id 的 checkpoint 残留
                    #         (如 Step 3 success)会导致 scheduler.run() 跳过 Step 3，test_cases 永远为 0。
                    #   通用性: 适用于所有审核恢复场景（需求审核→Step3，用例审核→Step4）。
                    #   合理性: 审核通过意味着当前步骤已完成，应从 paused_step+1 开始，不依赖 checkpoint。
                    #   覆盖性: 同步修复 case_review/_routes.py 的用例审核恢复逻辑。
                    resume_step = paused_step + 1
                    if resume_step > 5:
                        _log.warning(
                            "[审核] paused_step=%d 已是最后一步，无需恢复", paused_step
                        )
                        _log.info("[审核] 已批准 %d 条需求", approved)
                        return jsonify(
                            {"ok": True, "approved": approved, "message": "审核已批准"}
                        )
                    # ★ 清除该项目所有旧 checkpoint，避免残留导致跳过步骤
                    with guard("api_review_approve: from src.core.db_manager import ( ge", log=_log, level=logging.WARNING):
                        from src.core.db_manager import (
                            get_connection as _gc,
                            release_connection as _rc,
                        )

                        _conn = _gc()
                        try:
                            _conn.execute(
                                "DELETE FROM pipeline_checkpoints WHERE project=%s",
                                (project,),
                            )
                            _conn.commit()
                            _log.info(
                                "[审核] 已清除项目 %s 的所有旧 checkpoint", project
                            )
                        finally:
                            _rc(_conn)
                    run_id = uuid.uuid4().hex[:12]
                    with _active_runs_lock:
                        _active_runs[run_id] = {
                            "thread": None,
                            "force_stop": False,
                            "step": resume_step,
                            "project": project,
                            "start_ts": time.time(),
                        }
                    opts = StepRunOptions(
                        step_num=resume_step,
                        run_id=run_id,
                        step4_options={},
                        end_step=5,
                        resume=False,  # ★ 禁用 checkpoint 恢复，避免跳过步骤
                    )
                    t = threading.Thread(
                        target=_make_session_runner(_run_step, opts), daemon=True
                    )
                    with _active_runs_lock:
                        _active_runs[run_id]["thread"] = t
                    t.start()
                    _log.info(
                        "[审核] 已批准 %d 条，从 Step %d 恢复执行 (run_id=%s)",
                        approved,
                        resume_step,
                        run_id,
                    )
                    return jsonify(
                        {
                            "ok": True,
                            "approved": approved,
                            "message": "审核已批准",
                            "run_id": run_id,
                            "resume_step": resume_step,
                        }
                    )

            _log.info("[审核] 已批准 %d 条需求", approved)
            return jsonify({"ok": True, "approved": approved, "message": "审核已批准"})
        except Exception as e:
            _log.error("[审核] 批准异常: %s", str(e)[:120])
            return jsonify({"ok": False, "error": "审核批准失败"}), 500

    @exec_bp.route("/api/review/reject", methods=["POST"])
    def api_review_reject():
        """★ DB-only：审核驳回 — 将所有需求标记为 pending，触发重新拆解。"""
        from src.models.permission import check_admin_or_permission
        from src.backend.state import _current_project

        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )

        data = request.get_json(silent=True) or {}
        reason = data.get("reason", "")
        project = _current_project() or ""

        try:
            from src.core.excel_handler import excel_handler

            reqs = excel_handler.read_requirements()
            rejected = 0
            failed_req_ids = []
            for r in reqs:
                req_id = r.get("req_id", "")
                if not excel_handler.update_review_status(req_id, "rejected"):
                    failed_req_ids.append(req_id)
                    _log.warning("[review/reject] 审核状态更新失败: %s", req_id)
                else:
                    rejected += 1
            if failed_req_ids:
                _log.error(
                    "[review/reject] %d 条需求审核状态更新失败: %s",
                    len(failed_req_ids),
                    ",".join(failed_req_ids[:10]),
                )

            # ★ 记录审核日志
            with guard("api_review_reject: from src.backend.routes.case_review ", log=_log):
                from src.backend.routes.case_review import _log_review

                _log_review(project, "requirement", "batch", "reject", reason)

            _log.info("[审核] 已驳回 %d 条需求 (reason=%s)", rejected, reason[:60])
            return jsonify(
                {
                    "ok": True,
                    "rejected": rejected,
                    "message": "审核已驳回，需求将被重新拆解",
                }
            )
        except Exception as e:
            _log.error("[审核] 驳回异常: %s", str(e)[:120])
            return jsonify({"ok": False, "error": "审核驳回失败"}), 500


# ★ 模块加载时自动注册路由
_register_review_routes()
