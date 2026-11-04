# -*- coding: utf-8 -*-
"""
执行控制路由 Blueprint
=======================
URL 路径不变，从 app.py 提取。
"""

import time
from src.core.guarded import guard
import uuid
import json
import threading
from flask import Blueprint, jsonify, request
import logging

_log = logging.getLogger(__name__)

# ── 阈值常量 ──
_LOG_PREVIEW_BYTES = 30000
_CACHE_STALE_DAYS = 30

from src.backend.state import (
    _current_project,
    _is_running,
    _set_running,
    _set_force_stop,
    _set_paused_step,
    _get_paused_step,
    _active_runs,
    _active_runs_lock,
    _thinking_active,
    _get_version_warning,
)
from src.backend.executor import _run_step, _retry_failed


# ★ 步骤执行权限检查（两层模型 v3 — 系统角色权限 + 项目成员关系）
def _check_step_permission(step: int, project: str = None) -> tuple:
    """检查用户是否有执行指定步骤的权限。返回 (ok, error)。

    两层权限模型 v3：
    - admin（系统管理员）→ 所有 Step 均可
    - 项目级权限 → owner 全权，member 仅 step4-5
    - 系统级权限 + 项目成员身份 → 自定义角色场景（拥有系统权限且是项目成员）
    """
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin
    from src.models.permission import (
        has_permission,
        has_project_permission,
        can_execute_in_project,
    )

    uid = get_current_user()
    if not uid:
        return False, "请先登录"

    if not project:
        project = _current_project()
    if not project:
        return False, "未选择项目"

    # admin 短路全权
    if is_admin(uid):
        return True, ""

    # 按 step 映射到权限点
    perm_map = {
        1: "execution.step1",
        2: "execution.step2",
        3: "execution.step3",
        4: "execution.step4",
        5: "execution.step5",
    }
    perm = perm_map.get(step)
    if not perm:
        # 未知 step，回退到顶级总开关
        return can_execute_in_project(uid, project)

    # 项目级权限检查（owner 全权，member 限 step4-5）
    if has_project_permission(uid, project, perm):
        return True, ""

    # 系统级权限 + 项目成员身份（自定义角色场景）
    if has_permission(uid, perm):
        exec_ok, _ = can_execute_in_project(uid, project)
        if exec_ok:
            return True, ""

    # 明确提示
    if step in (1, 2, 3):
        return False, f"项目成员无权执行 Step {step}（步骤1-3仅项目管理员可执行）"
    return False, f"无权执行 Step {step}（需要权限: {perm}）"


exec_bp = Blueprint("execution", __name__)


@exec_bp.route("/api/browser-parallel-recommendation", methods=["GET"])
def api_browser_parallel_recommendation():
    ok, err = _check_step_permission(4)
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    try:
        configured = request.args.get("configured_max") or request.args.get(
            "max_parallel"
        )
        configured_max = int(configured) if configured not in (None, "") else None
    except Exception:
        configured_max = None
    try:
        from src.core.pipeline_config import get_browser_parallel_recommendation

        return jsonify(
            {"ok": True, **get_browser_parallel_recommendation(configured_max)}
        )
    except Exception as e:
        return (
            jsonify({"ok": False, "error": "并发推荐计算失败: %s" % str(e)[:200]}),
            500,
        )


@exec_bp.route("/api/step4-config", methods=["GET"])
def api_step4_config():
    ok, err = _check_step_permission(4)
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    try:
        from src.backend.helpers import (
            _load_case_asset_files,
            _load_browsers,
            _get_available_priorities,
        )
        from src.core.pipeline_config import (
            _read_config_value as _cfg,
            get_browser_parallel_recommendation,
        )

        return jsonify(
            {
                "ok": True,
                "case_asset_files": _load_case_asset_files(),
                "browsers": _load_browsers(),
                "priorities": _get_available_priorities(),
                "parallel": {
                    "max_parallel": int(_cfg("browser_max_parallel", 1)),
                    "auto_limit": bool(_cfg("browser_parallel_auto_limit", True)),
                    "reserved_mb": int(_cfg("browser_parallel_reserved_mb", 2048)),
                    "instance_mb": int(_cfg("browser_parallel_instance_mb", 800)),
                    "recommendation": get_browser_parallel_recommendation(),
                },
            }
        )
    except Exception as e:
        _log.exception("[step4-config] 获取配置失败")
        return (
            jsonify({"ok": False, "error": "Step4 配置加载失败: %s" % str(e)[:200]}),
            500,
        )


# ── Step4 运行选项持久化（与个性化配置同一套 user_credentials_v2 存储逻辑）──
# 用途："记忆上次运行选项"，用户级跨项目；流水线配置（pipeline-config）仍是配置编辑唯一事实源，
#       此处仅回显上次实际选择，避免每次重选，不构成新的配置编辑源。
_KEY_STEP4_RUN_OPTIONS = "STEP4_RUN_OPTIONS"


@exec_bp.route("/api/step4/run-options", methods=["GET"])
def api_step4_run_options_get():
    """读取用户上次 Step4 运行选项。"""
    from src.core.config_loader import get_current_user

    ok, err = _check_step_permission(4)
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    uid = get_current_user() or ""
    try:
        import json as _json

        if uid:
            from src.users.credentials import get_user_credential

            raw = get_user_credential(uid, _KEY_STEP4_RUN_OPTIONS, "*") or ""
            data = _json.loads(raw) if raw else {}
        else:
            data = {}
        return jsonify({"ok": True, "options": data if isinstance(data, dict) else {}})
    except Exception as e:
        _log.warning("[step4-options] 读取失败: %s", str(e)[:120])
        return jsonify({"ok": True, "options": {}})


@exec_bp.route("/api/step4/run-options", methods=["POST"])
def api_step4_run_options_save():
    """实时保存用户 Step4 运行选项（键 STEP4_RUN_OPTIONS）。"""
    from src.core.config_loader import get_current_user

    ok, err = _check_step_permission(4)
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    uid = get_current_user() or ""
    payload = request.get_json(silent=True) or {}
    opts = payload.get("options")
    if not isinstance(opts, dict):
        return jsonify({"ok": False, "error": "options 必须为对象"}), 400
    if not uid:
        return jsonify({"ok": False, "error": "未能获取当前用户"}), 401
    try:
        import json as _json

        from src.users.credentials import set_user_credential

        set_user_credential(
            uid, _KEY_STEP4_RUN_OPTIONS, _json.dumps(opts, ensure_ascii=False), "*"
        )
        return jsonify({"ok": True})
    except Exception as e:
        _log.warning("[step4-options] 保存失败: %s", str(e)[:120])
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


def _case_review_gate_status():
    """返回意图审核闸门状态，Step4 必须在审核通过后才能启动。"""
    try:
        from src.backend.routes.case_review import (
            _read_intents_for_review,
            _APPROVAL_RATE_THRESHOLD,
        )

        # 缺口路段判定：lane 持久层缺失（外部导入需求 / 未经 Step2）时按需求属性
        # 派生并回填，避免闸门在"确认率恒为 0"上永久死锁（不降级为全批全过）。
        try:
            from src.backend.routes.case_review._state import _current_project
            from src.core.versioning import current_version_id
            from src.pipeline.utils.review_lane_store import ensure_review_lanes

            _p = _current_project()
            if _p:
                _vid = int(current_version_id(_p) or 0)
                if _vid > 0:
                    ensure_review_lanes(_p, _vid)
        except Exception as _e:
            _log.warning("[review-gate] lane 派生失败(不阻断): %s", str(_e)[:100])

        intents, _source = _read_intents_for_review()
        total = len(intents)
        if total <= 0:
            return False, {"error": "尚无可审核的测试用例，请先完成 Step 3"}
        pending = sum(
            1 for c in intents if c.get("_review_status") in ("pending", None, "")
        )
        high_risk_pending = sum(
            1
            for c in intents
            if c.get("_risk_level") == "high"
            and c.get("_review_status") not in ("approved", "modified")
        )
        approval_rate = (total - pending) / max(total, 1) * 100
        if high_risk_pending > 0:
            return False, {
                "error": "意图审核未完成：还有 %d 条高风险测试用例未确认"
                % high_risk_pending,
                "high_risk_pending": high_risk_pending,
                "approval_rate": round(approval_rate, 1),
            }
        if approval_rate < _APPROVAL_RATE_THRESHOLD and pending > 0:
            return False, {
                "error": "意图审核未完成：确认率 %.0f%% 不足 90%%，还有 %d 条待确认"
                % (approval_rate, pending),
                "pending": pending,
                "approval_rate": round(approval_rate, 1),
            }
        return True, {
            "total": total,
            "pending": pending,
            "approval_rate": round(approval_rate, 1),
        }
    except Exception as e:
        return False, {"error": "意图审核状态检查失败: %s" % str(e)[:200]}


def _make_session_runner(target, *args, **kwargs):
    """包装后台线程入口：捕获当前请求线程的 session 上下文，注入到目标函数。
    解决 Flask 后台线程中 thread-local session 丢失的问题。

    ★ 关键修复：不仅捕获 thread-local 的 session_key，还要在请求线程显式解析一次
    config.ai_api_key / ai_provider / ai_base_url / ai_model。若前端仅把凭证存到
    user_credentials 而当前请求未触发 session 恢复，则 get_session_key() 为空，
    后台线程将无法靠 config.ai_api_key 自行恢复。此处强制捕获并写入后台线程。
    """
    from src.core.config_loader import (
        get_current_user,
        get_session_key,
        set_current_user,
        set_session_key,
    )
    from src.backend.state import (
        _set_current_project,
        _current_version,
        _set_current_version,
    )

    _captured_user = get_current_user()
    _captured_key = get_session_key()
    _captured_project = _current_project()
    _captured_version = _current_version()  # ★ 补传 version：threading.local() 不跨线程
    _captured_run_id = args[1] if len(args) > 1 else None

    # ★ 在请求线程解析最终生效的 AI 配置（含 DB 凭证兜底），避免后台线程再算一遍
    _resolved_key = _captured_key
    _resolved_provider = ""
    _resolved_base_url = ""
    _resolved_model = ""
    if _captured_user:
        with guard("_make_session_runner: from src.core.config_loader import c", log=_log):
            from src.core.config_loader import config

            _resolved_key = config.ai_api_key or _captured_key or ""
            _resolved_provider = config.ai_provider or ""
            _resolved_base_url = config.ai_base_url or ""
            _resolved_model = config.ai_model or ""

    def runner():
        try:
            if _captured_user:
                set_current_user(_captured_user)
            # ★ 优先使用请求线程解析出的最终 key；若仍为空，再尝试读 user_credentials
            _key_to_restore = _resolved_key or _captured_key or ""
            if _key_to_restore and _key_to_restore != "local":
                set_session_key(_key_to_restore)
            elif _captured_user:
                with guard("runner: from src.users.manager import get_us", log=_log):
                    from src.users.manager import get_user_credential

                    _db_key = get_user_credential(
                        _captured_user, "API_KEY", project="*"
                    )
                    if _db_key:
                        set_session_key(_db_key)
                        _log.info("[SessionRunner] 从 user_credentials 恢复 API Key")

            if _captured_project:
                _set_current_project(_captured_project)
                from src.core.config_loader import config
                from src.core.excel_handler import excel_handler

                config.reload(_captured_project)
                excel_handler.reload_paths()
                _log.info("[SessionRunner] 已恢复项目上下文: %s", _captured_project)
            else:
                raise RuntimeError("未选择项目，后台任务拒绝启动")

            if _captured_version:
                _set_current_version(_captured_version)  # ★ 恢复版本标签

            # ★ 诊断日志：确认后台线程已拿到 key（只打印掩码，安全）
            try:
                _restored_key = get_session_key() or ""
                _mask = (
                    _restored_key[:4] + "***" + _restored_key[-4:]
                    if len(_restored_key) > 8
                    else ("" if not _restored_key else "***")
                )
                _log.info(
                    "[SessionRunner] 后台线程 API Key 状态: %s",
                    "已恢复(" + _mask + ")" if _restored_key else "未恢复",
                )
            except Exception as _e:
                _log.debug("API Key 状态诊断失败: %s", _e, exc_info=True)

            target(*args, **kwargs)
        except BaseException as _e:
            # ★ 修复（2026-07-17）：捕获 BaseException 而非 Exception。
            # SystemExit/KeyboardInterrupt 继承自 BaseException，不被 except Exception 捕获。
            # 如果不捕获，_active_runs 会残留，后续同项目任务被 409 互斥永久阻断。
            import traceback, logging

            _l = logging.getLogger(__name__)
            _l.error("[CRASH] _run_step 异常 (%s): %s", type(_e).__name__, _e)
            _l.error(traceback.format_exc())
            # ★ [FIX P1-6] 同步清理 thinking 状态，防止残留
            try:
                import src.backend.state as _st

                _st._thinking_active = False
                _st._clear_thinking()  # ★ A1: 清除 per-project
            except Exception as _e:
                _log.debug("import + trace + trace: %s", _e, exc_info=True)
            if _captured_run_id:
                try:
                    from src.backend.log_capture import _broadcast_log

                    _broadcast_log(f"✗ 后台任务启动失败: {_e}", run_id=_captured_run_id)
                except Exception as _e:
                    _log.debug("import + import: %s", _e, exc_info=True)
                with _active_runs_lock:
                    _active_runs.pop(_captured_run_id, None)
            # ★ SystemExit/KeyboardInterrupt 不应继续传播（会杀掉 waitress 主进程）
            if isinstance(_e, (KeyboardInterrupt, SystemExit)):
                _l.warning(
                    "[CRASH] 捕获到 %s，已阻止传播（避免 waitress 主进程退出）",
                    type(_e).__name__,
                )
            else:
                raise
        finally:
            # ★ Fix: 后台线程连接兜底释放 — 模拟 Flask teardown_request，防止连接泄漏
            try:
                from src.core.db_manager._utils import release_thread_connections

                release_thread_connections()
            except Exception as _e:
                _log.debug("release_thread_connections 失败: %s", _e, exc_info=True)

    return runner


# ══════════════════════════════════════════════════════════
# ★ 以下路由已拆分到子模块（物理搬迁，零业务逻辑变更）
#   - preflight.py   → api_preflight
#   - run.py         → api_coverage_check, api_run_step, api_step4_run, api_run_all,
#                      api_stop, api_stop_run, api_pause, api_resume, api_retry_failed
#   - deployment.py  → api_deployment_docs, api_deployment_versions, api_last_run,
#                      api_deployment_preview
#   - status.py      → api_status, api_version_check_confirm, api_version_check_cancel
# ══════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════
# ★ 注册拆分子模块的路由（延迟导入，模块加载时自动注册）
# ══════════════════════════════════════════════════════════

from .review import _get_review_state  # noqa: E402, F811 — DB-only 审核状态查询
from . import review  # noqa: E402, F401 — 触发路由注册
from . import config  # noqa: E402, F401 — 触发路由注册
from . import preflight  # noqa: E402, F401 — 触发路由注册
from . import run  # noqa: E402, F401 — 触发路由注册
from . import deployment  # noqa: E402, F401 — 触发路由注册
from . import status  # noqa: E402, F401 — 触发路由注册
from . import pipeline_progress  # noqa: E402, F401 — ★ 流水线执行页面进度聚合路由
from . import client_browser  # noqa: E402, F401 — ★ 客户端浏览器 CDP 连接路由
from . import _pipeline_trace  # noqa: E402, F401 — ★ Pipeline trace 路由

# ══════════════════════════════════════════════════════════
# ★ 外部包路由注册：这些包通过 _register_routes() 向 exec_bp 添加路由。
#   必须在 exec_bp 被任何 Flask app 注册之前完成导入，
#   否则 Flask 3.x 的 _got_registered_once 锁会阻止 @exec_bp.route() 装饰器执行。
#   （pytest 全量测试时 R2 测试会先注册 exec_bp，导致后续 import 失败）
# ══════════════════════════════════════════════════════════
with guard("from src.backend.routes.case_review impo", log=_log):
    from src.backend.routes.case_review import _register_routes as _cr_rr  # noqa: F401
with guard("from src.backend.routes.oracle import _r", log=_log):
    from src.backend.routes.oracle import _register_routes as _or_rr  # noqa: F401
with guard("from src.backend.routes.nav_match import", log=_log):
    from src.backend.routes.nav_match import _register_routes as _nm_rr  # noqa: F401
with guard("from src.backend.routes.file_cleanup imp", log=_log):
    from src.backend.routes.file_cleanup import _register_routes as _fc_rr  # noqa: F401
with guard("from src.backend.routes.calibration impo", log=_log):
    from src.backend.routes.calibration import _register_routes as _cb_rr  # noqa: F401
