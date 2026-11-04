# -*- coding: utf-8 -*-
"""交互式需求澄清 API（clarification_bp）—— 流水线强化专项 T3。

★ 端点（url_prefix=/api/step2/clarify，design §7.4 新端点统一约定）：
    - GET  /sessions?project=&version_id=&run_id=       列出澄清会话
    - GET  /sessions/<session_id>                       会话详情（含已答问题）
    - GET  /questions?project=&version_id=&run_id=      /sessions 别名
    - POST /answers        {session_id, answers:[{question_index, answer}]}
    - POST /release        {session_id}
    - GET  /gate?project=&version_id=&run_id=           闸门状态查询

★ 响应格式：一律 {"ok": true, ...}；错误 {"ok": false, "error": str}。
★ 权限：读接口登录即可；写接口（answers/release）要求 execution.run 权限，
   answered_by 取当前登录用户（data-perm 鉴权 + 后端二次校验）。
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from src.core.config_loader import get_current_user

_log = logging.getLogger(__name__)

clarification_bp = Blueprint("clarification", __name__, url_prefix="/api/step2/clarify")

# ── 权限装饰器（复用 case_upload._decorators 模式）──
from functools import wraps  # noqa: E402

from src.models.permission import has_permission  # noqa: E402


def _login_required(fn):
    """要求登录（未登录返回 401）。"""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        uid = get_current_user()
        if not uid:
            return jsonify({"ok": False, "error": "请先登录"}), 401
        return fn(*args, **kwargs)

    return wrapper


def _require_perm(perm: str):
    """要求系统级权限（无权限返回 403）。"""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            uid = get_current_user()
            if not uid:
                return jsonify({"ok": False, "error": "请先登录"}), 401
            if not has_permission(uid, perm):
                return jsonify(
                    {"ok": False, "error": f"无权执行此操作（需要权限: {perm}）"}
                ), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def _service():
    """延迟导入 ClarificationService（避免顶层 DB 依赖）。"""
    from src.pipeline.steps.step2.clarification_service import ClarificationService

    return ClarificationService()


@clarification_bp.route("/sessions", methods=["GET"])
@_login_required
def api_list_sessions():
    """列出澄清会话（歧义卡片 + 证据 + 澄清问题 + 状态）。"""
    project = (request.args.get("project") or "").strip()
    version_id = (request.args.get("version_id") or "").strip()
    run_id = (request.args.get("run_id") or "").strip()
    items = _service().list_sessions(project=project, version_id=version_id, run_id=run_id)
    return jsonify({"ok": True, "items": items, "count": len(items)})


@clarification_bp.route("/sessions/<session_id>", methods=["GET"])
@_login_required
def api_get_session(session_id: str):
    """查询单个会话详情（含已答问题）。"""
    sess = _service().get_session(session_id)
    if sess is None:
        return jsonify({"ok": False, "error": "会话不存在"}), 404
    return jsonify({"ok": True, "session": sess})


@clarification_bp.route("/questions", methods=["GET"])
@_login_required
def api_list_questions():
    """design §3.3 兼容别名：/questions == /sessions。"""
    return api_list_sessions()


@clarification_bp.route("/answers", methods=["POST"])
@_require_perm("execution.run")
def api_submit_answers():
    """提交回答：回填 requirements 并置会话为 answered（闸门解除前置）。"""
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id") or "").strip()
    answers = payload.get("answers") or []
    if not session_id:
        return jsonify({"ok": False, "error": "缺少 session_id"}), 400
    if not isinstance(answers, list) or not answers:
        return jsonify({"ok": False, "error": "answers 为空"}), 400
    answered_by = get_current_user() or "unknown"
    result = _service().submit_answer(
        session_id, answers, answered_by=answered_by
    )
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error") or "提交失败"}), 400
    return jsonify(
        {
            "ok": True,
            "session_status": result.get("session_status"),
            "backfill": result.get("backfill"),
            "backfill_ok": result.get("backfill_ok", False),
        }
    )


@clarification_bp.route("/release", methods=["POST"])
@_require_perm("execution.run")
def api_release_gate():
    """解除某会话的生成闸门（status → released）。"""
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return jsonify({"ok": False, "error": "缺少 session_id"}), 400
    result = _service().release_gate(session_id)
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error") or "解除闸门失败"}), 400
    return jsonify(
        {
            "ok": True,
            "gate": result.get("gate"),
            "can_generate": result.get("can_generate", False),
        }
    )


@clarification_bp.route("/gate", methods=["GET"])
@_login_required
def api_gate_status():
    """查询闸门是否仍打开（should_block_generation 的实时状态）。"""
    project = (request.args.get("project") or "").strip()
    version_id = (request.args.get("version_id") or "").strip()
    run_id = (request.args.get("run_id") or "").strip()
    svc = _service()
    gate_open = svc.is_gate_open(project, version_id, run_id)
    sessions = svc.list_sessions(project=project, version_id=version_id, run_id=run_id)
    blocking_total = sum(1 for s in sessions if s.get("blocking"))
    released = sum(1 for s in sessions if s.get("status") == "released")
    return jsonify(
        {
            "ok": True,
            "gate_open": gate_open,
            "blocking_total": blocking_total,
            "released": released,
            "total_sessions": len(sessions),
        }
    )
