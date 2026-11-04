# -*- coding: utf-8 -*-
"""
诊断/探针路由 — detailed / metrics / healthz / readyz

【2026-06-18 拆分】从 health.py 物理搬迁，零业务逻辑变更。
"""

from flask import jsonify
from src.core.guarded import guard
import logging

_log = logging.getLogger(__name__)

from src.backend.state import _current_project
from .health import health_bp


# ═══════════════════════════════════════════════════════════════
# GET /api/health/detailed — 外部依赖健康状态详情
# ═══════════════════════════════════════════════════════════════


@health_bp.route("/api/health/detailed")
def api_health_detailed():
    try:
        from src.models.permission import check_admin_or_permission

        ok, err = check_admin_or_permission("health.view", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )

        from src.extensions.health_checker import health_checker

        checks = health_checker.run_all_checks(use_cache=True)
        up = sum(1 for c in checks if c["status"] == "up")

        # ★ RAG 语义引擎状态（自动检测，前端可见）
        embedding_status = None
        try:
            from src.extensions.semantic_index import get_model_status

            embedding_status = get_model_status()
            if embedding_status and embedding_status.get("cache_path") is None:
                embedding_status["cache_path"] = ""
        except Exception as _e:
            _log.debug("import + health + check: %s", _e, exc_info=True)

        return jsonify(
            {
                "ok": True,
                "overall": (
                    "healthy"
                    if up == len(checks)
                    else "degraded" if up > 0 else "unhealthy"
                ),
                "up_count": up,
                "total": len(checks),
                "checks": checks,
                "embedding": embedding_status,
            }
        )
    except Exception as e:
        _log.error("[api_health_detailed] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


# ═══════════════════════════════════════════════════════════════
# GET /api/health/metrics — Prometheus 格式指标
# ═══════════════════════════════════════════════════════════════


@health_bp.route("/api/health/metrics")
def api_health_metrics():
    try:
        from src.extensions.metrics_collector import metrics_collector

        data = metrics_collector.get_prometheus_metrics()
        if data:
            # 对正常指标返回 text/plain；为了一致性添加 ok 头（非 JSON，不便直接加 ok）
            import flask as _f

            return _f.Response(
                data, mimetype="text/plain; version=0.0.4; charset=utf-8"
            )
        # 降级：返回空响应 + 提示
        import flask as _f

        return _f.Response(
            "# prometheus-client 未安装，指标不可用\n", mimetype="text/plain"
        )
    except Exception as e:
        _log.error("[api_health_metrics] 异常: %s", e, exc_info=True)
        # 非 JSON 端点出错时，仍尝试返回 JSON 错误提示
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


# ═══════════════════════════════════════════════════════════════
# ★ B3: /healthz — 进程级 liveness 探针（毫秒级返回，不检查依赖）
# ═══════════════════════════════════════════════════════════════


@health_bp.route("/healthz")
def healthz():
    """k8s/LB 风格 liveness probe：仅证明进程存活，不查 DB/Redis。"""
    import time as _t

    return jsonify({"status": "ok", "ts": int(_t.time())})


# ═══════════════════════════════════════════════════════════════
# ★ B3: /readyz — 就绪探针（检查 DB 连接，失败 503）
# ═══════════════════════════════════════════════════════════════


@health_bp.route("/readyz")
def readyz():
    """readiness probe：检查 DB 是否能连。失败返回 503。"""
    import time as _t

    db_ok = False
    err_msg = ""
    conn = None
    try:
        from src.models.base import get_conn as _db_get_conn

        try:
            conn = _db_get_conn()
            # 轻量探测：execute SELECT 1
            try:
                conn.execute("SELECT 1").fetchone()
                db_ok = True
            except Exception as ee:
                err_msg = str(ee)[:200]
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("execute + execute + fetchone: %s", _e, exc_info=True)
    except Exception as e:
        err_msg = str(e)[:200]

    # ★ 就绪时触发全局离线回放：MySQL 恢复后把积压的离线数据自动同步回主库，
    #   避免仅依赖业务写路径的机会性回放导致恢复初期数据仍分叉。
    if db_ok:
        with guard("readyz: from src.core import offline_store", log=_log, level=logging.WARNING):
            from src.core import offline_store

            offline_store.replay_all()

    payload = {
        "status": "ok" if db_ok else "fail",
        "db": "up" if db_ok else "down",
        "ts": int(_t.time()),
    }
    if not db_ok and err_msg:
        payload["error"] = err_msg
    return (jsonify(payload), 200 if db_ok else 503)
