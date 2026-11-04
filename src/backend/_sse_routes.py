# -*- coding: utf-8 -*-
"""
SSE 实时日志流 + Prometheus 指标 + 前端入口路由（从 app.py 拆分）。

包含：/metrics、/api/log-stream、/api/log-history、/api/log-history/<run_id>。
旧 SPA 入口（index.html + app.js）已删除，根路径 "/" 重定向到 /dashboard。
"""

import logging
from src.core.guarded import guard
import queue
from pathlib import Path
from flask import request, Response, jsonify, stream_with_context, redirect
from ._ring_buffer import RingBuffer

_log = logging.getLogger(__name__)


# ── Prometheus /metrics 端点（健康看板跳转目标）──
def prometheus_metrics():
    """Prometheus 文本格式指标端点。供健康看板按钮打开新标签页查看。"""
    from flask import Response

    try:
        from src.extensions.metrics_collector import metrics_collector

        data = metrics_collector.get_prometheus_metrics()
        if data:
            return Response(data, mimetype="text/plain; version=0.0.4; charset=utf-8")
        return Response(
            "# prometheus-client 未安装，指标不可用\n", mimetype="text/plain"
        )
    except ImportError:
        return Response(
            "# prometheus-client 未安装，指标不可用\n", mimetype="text/plain"
        )
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            "[metrics] Prometheus 指标获取异常: %s", str(e)[:120]
        )
        return Response(f"# 指标服务异常: {str(e)[:120]}\n", mimetype="text/plain")


# ── 根路径重定向（旧 SPA 入口已移除，根路径直接进入仪表盘）──
def root_redirect():
    """旧 SPA 入口 (index.html) 已删除，"/" 重定向到 /dashboard。"""
    return redirect("/dashboard", code=302)


# ── 多页面架构：通用页面渲染（Jinja2 模板继承）──
# 用法：render_page("login", active_nav="login") → 渲染 templates/login.html
# 模板内通过 {% extends "_layout_base.html" %} 继承公共布局
def render_page(template_name, **context):
    """渲染指定模板（支持 Jinja2 继承），附加 no-cache 头。"""
    from flask import render_template, make_response

    response = make_response(render_template(template_name, **context))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ── 各页面路由处理函数（薄封装，便于 app.py 注册）──
def page_login():
    return render_page("login.html", active_nav="login")


def page_register():
    return render_page("register.html", active_nav="register")


def page_dashboard():
    return render_page("dashboard.html", active_nav="dashboard")


def page_pipeline():
    return render_page("pipeline.html", active_nav="pipeline")


def page_docs():
    return render_page("docs.html", active_nav="docs")


def page_charts():
    return render_page("charts.html", active_nav="charts")


def page_metrics_page():
    return render_page("metrics.html", active_nav="metrics")


def page_logs():
    """★ 旧 Jinja 日志页已由 React `/log-panel` 取代，此处 302 重定向保留旧外链兼容。"""
    qs = request.query_string.decode("utf-8", "ignore")
    return redirect("/log-panel" + ("?" + qs if qs else ""), code=302)


def page_quality_panel():
    """★ T04/P1-4+P1-5：W3 质量治理面板（只读卡片 + 开关区）。"""
    return render_page("quality-panel.html", active_nav="quality-panel")


def page_action_lib():
    return render_page("action-lib.html", active_nav="action-lib")


def page_test_methods():
    """★ 测试方法论清单页（指标区）：卡片式展示 12 种方法论 + 双口径三桶统计。"""
    return render_page("test-methods.html", active_nav="test-methods")


def page_project_mgmt():
    return render_page("project-mgmt.html", active_nav="project-mgmt")


def page_new_project():
    return render_page("new-project.html", active_nav="new-project")


def page_version_mgmt():
    return render_page("version-mgmt.html", active_nav="version-mgmt")


def page_user_mgmt():
    return render_page("user-mgmt.html", active_nav="user-mgmt")


def page_role_mgmt():
    return render_page("role-mgmt.html", active_nav="role-mgmt")


def page_change_password():
    return render_page("change-password.html", active_nav="change-password")


def page_personal_config():
    return render_page("personal-config.html", active_nav="personal-config")


def page_config():
    return render_page("config.html", active_nav="config")


def page_system_config():
    return render_page("system-config.html", active_nav="system-config")


def page_prompt_templates():
    return render_page("prompt-templates.html", active_nav="prompt-templates")


def page_config_history():
    return render_page("config-history.html", active_nav="config-history")


def page_pipeline_config():
    return render_page("pipeline-config.html", active_nav="pipeline-config")


def page_skill_rules():
    """★ Skill 库：算法规则清单（Step1-5 规则元信息，只读）。"""
    return render_page("skill-rules.html", active_nav="skill-rules")


def page_step4_config():
    return render_page("step4-config.html", active_nav="step4-config")


def page_file_upload():
    return render_page("file-upload.html", active_nav="file-upload")


def page_clear_files():
    return render_page("clear-files.html", active_nav="clear-files")


def page_intent_edit():
    return render_page("intent-edit.html", active_nav="intent-edit")


def page_intent_review():
    return render_page("intent-review.html", active_nav="intent-review")


def page_review():
    return render_page("review.html", active_nav="review")


def page_confidence_block():
    return render_page("confidence-block.html", active_nav="confidence-block")


def page_datasource():
    """★ 数据源已并入「项目配置 → 数据源配置」区块（2026-08-31），独立页改为重定向到项目配置"""
    return redirect("/config", code=302)


# ── 旧数据源页面 302 重定向到 /config（数据源配置区块，保留外链兼容）──
def page_ds_api():
    return redirect("/config", code=302)


def page_ds_database():
    return redirect("/config", code=302)


def page_ds_design():
    return redirect("/config", code=302)


def page_ds_requirements():
    return redirect("/config", code=302)


def page_ds_target():
    return redirect("/config", code=302)


def page_ai_health():
    return render_page("ai-health.html", active_nav="ai-health")


def page_diagnostics():
    return render_page("diagnostics.html", active_nav="diagnostics")


def page_nav_tree():
    return render_page("nav-tree.html", active_nav="nav-tree")


def page_audit_logs():
    return render_page("audit-logs.html", active_nav="audit-logs")


def page_ci_integration():
    return render_page("ci-integration.html", active_nav="ci-integration")


def page_export_page():
    return render_page("export-page.html", active_nav="export-page")


def page_step3_case_upload():
    """★ 用例绑定专项(2026-08): Step3 用例准备页（下载模板 + 上传用例 + 匹配状态）。"""
    return render_page("step3-case-upload.html", active_nav="step3-case-upload")


def page_clarify():
    """★ T3 流水线强化专项: 交互式需求澄清问答页（歧义卡片 + 证据 + 作答 + 解除闸门）。"""
    return render_page("clarify.html", active_nav="clarify")


# ── 审计日志 API（login_tracking / action_logs / rag_usage_log）──
def api_audit_logs():
    """GET /api/audit-logs?type=login|action|rag — 查询审计日志列表。"""
    log_type = (request.args.get("type") or "action").strip().lower()
    limit = min(int(request.args.get("limit", 100)), 500)
    offset = max(int(request.args.get("offset", 0)), 0)
    try:
        if log_type == "login":
            from src.users.audit_logs._action_logs import (
                list_login_tracking,
                count_login_tracking,
            )

            user_id = (request.args.get("user_id") or "").strip()
            action = (request.args.get("action") or "").strip()
            items = list_login_tracking(
                user_id=user_id, action=action, limit=limit, offset=offset
            )
            total = count_login_tracking(user_id=user_id, action=action)
        elif log_type == "rag":
            from src.pipeline.step3._modeling_engine import (
                list_rag_usage_log,
                count_rag_usage_log,
            )

            project = (request.args.get("project") or "").strip()
            case_id = (request.args.get("case_id") or "").strip()
            rag_source_type = (request.args.get("rag_source_type") or "").strip()
            items = list_rag_usage_log(
                project=project,
                case_id=case_id,
                rag_source_type=rag_source_type,
                limit=limit,
                offset=offset,
            )
            total = count_rag_usage_log(
                project=project, case_id=case_id, rag_source_type=rag_source_type
            )
        else:
            from src.users.audit_logs._action_logs import (
                list_action_logs,
                count_action_logs,
            )

            scope_type = (request.args.get("scope_type") or "").strip()
            scope_id = (request.args.get("scope_id") or "").strip()
            user_id = (request.args.get("user_id") or "").strip()
            action = (request.args.get("action") or "").strip()
            items = list_action_logs(
                scope_type=scope_type,
                scope_id=scope_id,
                user_id=user_id,
                action=action,
                limit=limit,
                offset=offset,
            )
            total = count_action_logs(
                scope_type=scope_type,
                scope_id=scope_id,
                user_id=user_id,
                action=action,
            )
        return jsonify({"ok": True, "items": items, "total": total})
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            "[audit-logs] 查询失败: %s", str(e)[:120], exc_info=True
        )
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


# ── 日志面板（React SPA，构建产物部署在 static/log-panel/）──
def page_log_panel():
    """日志面板页（嵌入主壳，保留侧边栏/顶栏）。

    ★ 根因(2026-08-31)：原 /log-panel 直接返回独立全屏 React 应用，进入后主侧边栏
       被整体替换、日志面板排序与其他页面不一致。现改为渲染继承主壳的模板，
       以 iframe 承载 React 应用（见 templates/log-panel.html）。
    """
    return render_page("log-panel.html", active_nav="log-panel")


def log_panel_app():
    """返回 React 日志面板入口（路径 /log-panel，供 /realtime-logs 页 iframe 内嵌）。

    ★ 根因(2026-08-31)：React 产物已构建且无源码可重建，其 bundle 内建
       `<BrowserRouter basename="/log-panel">`，客户端只在 URL 因数含前缀 "/log-panel"、
       且去掉该前缀后命中唯一路由 "/" 时才渲染（即完整路径恰为 /log-panel 才出内容）。
       先前把该 SPA 挂在 /log-panel/app 等子路径都会「虚路由」整页空白——本功能本从未渲染。
       修正：本次改由 /realtime-logs 承接主壳包装，/log-panel 原样承载 React（basename 即命中）。
       故此处只原样返回 index.html，不再做任何 location 注入。
    """
    from flask import make_response

    _frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    html = (_frontend_dir / "static" / "log-panel" / "index.html").read_text(
        encoding="utf-8"
    )
    response = make_response(html)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ── SSE 实时日志流 ──
def api_log_stream():
    from src.backend.state import (
        _sse_queues,
        _sse_queues_lock,
        _run_sse_queues,
        _run_sse_queues_lock,
    )

    run_id = request.args.get("run_id", "")
    # ★ 安全修复：使用 RingBuffer（环形缓冲区）替代 queue.Queue(maxsize=1000)
    # 当生产速度 > 消费速度时，自动丢弃最旧日志，永不阻塞执行线程。
    q = RingBuffer(maxsize=1000)
    if run_id:
        with _run_sse_queues_lock:
            if run_id not in _run_sse_queues:
                _run_sse_queues[run_id] = []
            _run_sse_queues[run_id].append(q)
    else:
        with _sse_queues_lock:
            _sse_queues.append(q)

    def generate():
        try:
            while True:
                try:
                    # ★ 心跳间隔 5 秒：服务端在 5 秒内感知客户端 close()，加速 TCP 连接释放
                    payload = q.get(timeout=5)
                    yield f"data: {payload}\n\n"
                except queue.Empty:
                    # ★ 修复：纯内存心跳，移除 health_checker 同步调用（原实现阻塞 15 秒，6 个外部 HTTP）
                    # health_summary 由前端通过 /api/health-summary 独立拉取（带缓存），不再通过 SSE 推送
                    yield 'data: {"heartbeat": true}\n\n'
        except GeneratorExit:
            if run_id:
                with _run_sse_queues_lock:
                    try:
                        _run_sse_queues.get(run_id, []).remove(q)
                    except (ValueError, KeyError) as _e:
                        _log.debug(
                            "[sse_routes] 移除失效 per-run 队列异常: %s",
                            _e,
                            exc_info=True,
                        )
            else:
                with _sse_queues_lock:
                    try:
                        _sse_queues.remove(q)
                    except ValueError as _e:
                        _log.debug(
                            "[sse_routes] 移除失效 SSE 队列异常: %s", _e, exc_info=True
                        )

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 日志历史 ──
def api_log_history():
    from src.backend.state import _all_log_lines, _all_log_lines_lock

    with _all_log_lines_lock:
        return jsonify(list(_all_log_lines[-200:]))


def api_log_runs():
    """列出当前项目最近的 run_id 列表（供日志面板运行历史切换）。

    返回: [{"run_id": "...", "log_count": N, "first_ts": "...", "last_ts": "..."}]
    """
    try:
        from src.core.config_loader import config
        from src.core.db_manager import get_connection, release_connection

        project_name = getattr(config, "project_name", "")
        conn = None
        try:
            conn = get_connection()
            rows = conn.execute(
                "SELECT run_id, COUNT(*) AS cnt, MIN(created_at) AS first_ts, MAX(created_at) AS last_ts "
                "FROM run_logs WHERE project = %s AND run_id <> '' "
                "GROUP BY run_id ORDER BY MAX(id) DESC LIMIT 50",
                (project_name,),
            ).fetchall()
            runs = [
                {
                    "run_id": str(r["run_id"]),
                    "log_count": int(r["cnt"]),
                    "first_ts": str(r["first_ts"]) if r["first_ts"] else "",
                    "last_ts": str(r["last_ts"]) if r["last_ts"] else "",
                }
                for r in (rows or [])
            ]
            return jsonify({"runs": runs, "total": len(runs)})
        finally:
            release_connection(conn)
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning("[log-runs] 查询运行历史失败: %s", e)
        return jsonify({"runs": [], "total": 0, "error": str(e)[:200]})


def api_current_run_id():
    """返回当前 session 对应的确定性 run_id（项目+版本）。

    前端重启后无需记忆随机 UUID，调用此接口即可拿到当前项目+版本的 run_id，
    用于查询历史日志和订阅 SSE 实时流。

    返回: {"run_id": "某SPA系统__v1.0", "project": "某SPA系统", "version_label": "v1.0"}
    """
    try:
        from src.core.config_loader import config
        from src.core.versioning import current_run_id, current_version_label

        project = getattr(config, "project_name", "") or ""
        vlabel = current_version_label(project) if project else ""
        rid = current_run_id(project)
        return jsonify(
            {
                "run_id": rid,
                "project": project,
                "version_label": vlabel,
            }
        )
    except Exception as e:
        _log.warning("[current-run-id] 生成失败: %s", e)
        return jsonify(
            {"run_id": "", "project": "", "version_label": "", "error": str(e)[:200]}
        )


def api_log_history_run(run_id):
    """按 run_id 分页查询日志。

    Query params:
        - limit: 每页条数，默认 200，最大 2000
        - offset: 偏移量，默认 0
        - order: asc（默认，按时间正序） / desc（按时间倒序）

    Returns:
        {"run_id": ..., "total": int, "offset": int, "limit": int,
         "has_more": bool, "logs": [{ts, text, step, level}, ...]}
    """
    from flask import request
    from src.backend.state import _run_logs, _run_logs_lock

    try:
        limit = min(int(request.args.get("limit", 200)), 2000)
        offset = max(int(request.args.get("offset", 0)), 0)
        order = request.args.get("order", "asc").lower()
        if order not in ("asc", "desc"):
            order = "asc"
    except Exception:
        limit, offset, order = 200, 0, "asc"

    conn = None
    try:
        from src.core.db_manager import get_connection
        from src.core.config_loader import config

        project = getattr(config, "project_name", "") or ""
        conn = get_connection()

        # 先取总数（按 project + run_id 隔离，防止跨项目读取）
        total_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM run_logs WHERE run_id = %s AND project = %s",
            (str(run_id), str(project)),
        ).fetchone()
        total = int(total_row["cnt"]) if total_row else 0

        # 分页查询：按 id 排序保证顺序稳定
        # order 已由上方校验为 asc/desc，此处直接拼接 SQL 关键字（占位符会被 MySQL 解释为字符串导致语法错误）
        order_sql = "ASC" if order == "asc" else "DESC"
        cur = conn.execute(
            f"SELECT message, created_at, step_num, log_level FROM run_logs "
            f"WHERE run_id = %s AND project = %s ORDER BY id {order_sql} LIMIT %s OFFSET %s",
            (str(run_id), str(project), limit, offset),
        )
        rows = cur.fetchall() if cur else []

        logs = []
        for r in rows or []:
            if not r:
                continue
            _step_num = int(r.get("step_num", 0) or 0)
            _step = "step%d" % _step_num if 1 <= _step_num <= 5 else "system"
            _level = str(r.get("log_level", "INFO") or "INFO").upper()
            if _level == "WARNING":
                _level = "WARN"
            logs.append(
                {
                    "ts": str(r["created_at"]),
                    "text": r["message"],
                    "step": _step,
                    "level": _level,
                }
            )

        # 回填内存供后续 SSE 续写（保留最近 500 条，避免内存无限增长）
        with _run_logs_lock:
            if run_id not in _run_logs:
                _run_logs[run_id] = []
            if logs:
                _run_logs[run_id] = (logs + _run_logs[run_id])[-500:]

        return jsonify(
            {
                "run_id": run_id,
                "total": total,
                "offset": offset,
                "limit": limit,
                "has_more": (offset + len(logs)) < total,
                "logs": logs,
            }
        )
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning("[log-history] MySQL 查询失败: %s", e)
        return jsonify(
            {
                "run_id": run_id,
                "total": 0,
                "offset": offset,
                "limit": limit,
                "has_more": False,
                "logs": [],
                "error": str(e)[:200],
            }
        )
    finally:
        if conn:
            with guard("api_log_history_run: conn.close()", log=_log):
                conn.close()
