# -*- coding: utf-8 -*-
"""
中间件函数（从 app.py 拆分）。

包含：缓存控制、数据库连接释放、API 耗时指标、登录守卫、滑动续期。
所有函数保持原实现不变，由 app.py 通过 app.after_request / before_request /
teardown_request 注册。
"""

import time
from src.core.guarded import guard
import logging
import json
from flask import request, jsonify, session, Response

_log = logging.getLogger(__name__)


# ── ★ 全局 GET API 响应缓存（30 秒 TTL）──
# 控制台一次加载 21 个 API，每个 600-2000ms，总耗时 11-26s。
# 30 秒 TTL 确保页面加载期间（3-4s）写入的缓存在下次切换时仍然有效。
# POST/PUT/DELETE 请求会立即清空全部缓存，保证写入后数据一致。
_api_response_cache = {}  # {cache_key: (timestamp, (status, headers, body))}
_API_CACHE_TTL = 30  # 秒

# 不缓存的 API 路径（需实时或为流式接口）
# - /api/status          : 前端轮询用，需实时返回 running/paused 状态
# - /api/log-stream      : SSE 长连接
# - /api/pipeline-progress : 有活跃任务时需实时
# - /api/sse             : SSE 流
# - /api/health-summary  : 健康检查可能需实时
_API_CACHE_BLACKLIST = (
    "/api/log-stream",
    "/api/sse",
    "/api/events",
)


def _api_cache_key():
    """生成缓存 key：(path, query, user, project)。

    ★ 直接从 Flask session 读取 user_id 和 project_name，不依赖 thread-local
      （此时 _require_login 尚未执行，但 Flask session 已从 cookie 加载完毕）。
    ★ 含 project：避免同一用户切换项目后 30s 内仍返回旧项目数据。
      project_name 在 session 中是稳定字段（不会在 before_request 阶段被修改），
      仅 thread-local 会在 _require_login 中被同步，故 session 读取安全。
    """
    path = request.path
    query = request.query_string.decode("utf-8", "ignore")
    # 直接从 session 读取（Flask 每个 request 自动加载 cookie session）
    uid = session.get("user_id") or "anon"
    proj = session.get("project_name") or ""
    return (path, query, uid, proj)


def _is_api_cacheable():
    """判断当前请求是否可缓存：GET + /api/ + 非黑名单 + 非活跃任务。"""
    if request.method != "GET":
        return False
    path = request.path
    if not path.startswith("/api/"):
        return False
    # 黑名单
    for bl in _API_CACHE_BLACKLIST:
        if path.startswith(bl):
            return False
    # /api/status 始终实时（轮询用）
    if path == "/api/status":
        return False
    # /api/pipeline-progress 有活跃任务时不缓存
    if path == "/api/pipeline-progress":
        try:
            from src.backend.state import _active_runs, _active_runs_lock

            with _active_runs_lock:
                if len(_active_runs) > 0:
                    return False
        except Exception as _e:
            _log.debug("[middleware] 检查活跃任务异常: %s", _e, exc_info=True)
    return True


def try_api_cache():
    """before_request 钩子：命中缓存直接返回。"""
    if not _is_api_cacheable():
        # ★ 修复：/api/status 走 1 秒短缓存，减少高频轮询的 DB 压力
        if request.path == "/api/status":
            with guard("try_api_cache: key = _api_cache_key()", log=_log):
                key = _api_cache_key()
                cached = _status_short_cache.get(key)
                if cached and (time.time() - cached[0]) < 1.0:
                    status, headers, body = cached[1]
                    resp = Response(body, status=status, headers=dict(headers))
                    resp.headers["X-API-Cache"] = "HIT-1s"
                    return resp
        return None
    with guard("try_api_cache: key = _api_cache_key()", log=_log):
        key = _api_cache_key()
        cached = _api_response_cache.get(key)
        if cached and (time.time() - cached[0]) < _API_CACHE_TTL:
            status, headers, body = cached[1]
            resp = Response(body, status=status, headers=dict(headers))
            resp.headers["X-API-Cache"] = "HIT"
            return resp
    return None


# ★ 修复：/api/status 专用 1 秒短缓存（避免高频轮询打满 DB）
_status_short_cache = {}


def save_api_cache(response):
    """after_request 钩子：缓存成功响应。

    ★ 若响应已带 X-API-Cache: HIT（来自 try_api_cache 命中），直接返回不处理，
      避免 after_request 在 before_request 返回缓存响应时仍然执行、把 HIT 头覆盖成 MISS。
    """
    if response.headers.get("X-API-Cache", "").startswith("HIT"):
        return response
    # ★ 修复：/api/status 走 1 秒短缓存
    if request.path == "/api/status":
        if response.status_code != 200:
            return response
        with guard("save_api_cache: key = _api_cache_key()", log=_log):
            key = _api_cache_key()
            body = response.get_data()
            if len(body) > 1024 * 1024:
                return response
            headers = {
                k: v
                for k, v in response.headers.items()
                if k.lower() not in ("content-length", "date", "server")
            }
            _status_short_cache[key] = (
                time.time(),
                (response.status_code, headers, body),
            )
            response.headers["X-API-Cache"] = "MISS-1s"
        return response
    if not _is_api_cacheable():
        return response
    # 只缓存 200 响应
    if response.status_code != 200:
        return response
    with guard("save_api_cache: key = _api_cache_key()", log=_log):
        key = _api_cache_key()
        # 获取响应体
        body = response.get_data()
        # 只缓存 <1MB 的响应
        if len(body) > 1024 * 1024:
            return response
        headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in ("content-length", "date", "server")
        }
        _api_response_cache[key] = (time.time(), (response.status_code, headers, body))
        response.headers["X-API-Cache"] = "MISS"
    return response


def invalidate_api_cache():
    """清空全部 API 缓存（供 POST/PUT/DELETE 操作后调用）。"""
    _api_response_cache.clear()
    _status_short_cache.clear()


def invalidate_api_cache_for_path(path_prefix):
    """清空指定路径前缀的缓存。"""
    keys_to_del = [k for k in _api_response_cache if k[0].startswith(path_prefix)]
    for k in keys_to_del:
        _api_response_cache.pop(k, None)


def invalidate_api_cache_for_user(uid=None):
    """清空指定用户的所有 API 缓存（切换项目时调用，避免旧项目数据残留）。

    ★ 只清该用户的缓存，不影响其他用户，性能影响最小。
    ★ 同时清理 /api/status 短缓存（也按用户隔离）。
    """
    if not uid:
        try:
            uid = session.get("user_id") or "anon"
        except Exception:
            uid = "anon"
    # 清理主缓存：cache_key = (path, query, uid, proj)，第3位是 uid
    keys_to_del = [k for k in _api_response_cache if k[2] == uid]
    for k in keys_to_del:
        _api_response_cache.pop(k, None)
    # 清理 status 短缓存
    keys_to_del2 = [k for k in _status_short_cache if k[2] == uid]
    for k in keys_to_del2:
        _status_short_cache.pop(k, None)


def invalidate_cache_on_write():
    """before_request 钩子：非 GET 请求清空全部 API 缓存。

    ★ 简化策略：任何写操作都可能影响多个 API 返回的数据，
    统一清空最安全（5 秒 TTL 的代价可接受）。
    """
    if request.method != "GET" and request.path.startswith("/api/"):
        _api_response_cache.clear()
        _status_short_cache.clear()


# ── 全局安全头 + 禁用浏览器缓存（服务端缓存仍生效）──
# ★ bg-image 路径允许浏览器缓存（ETag 驱动 304），避免个性化页 20+ 并发请求打爆连接池
_BG_CACHE_PATHS = ("/api/bg-image",)

# ★ 动态渲染的 HTML 页面：必须禁用浏览器缓存，
#   否则步骤切换/数据更新后浏览器仍展示旧 HTML（如步骤2/3/4 列表残留旧内容）。
#   这些页面每次请求都由 Jinja 渲染，refresh 时需强制拉取最新版本。
#   现在对所有非 API/static/bg-image 的页面统一禁用缓存，见下方 else 分支。


def _no_cache(response):
    path = request.path
    is_bg_image = any(path.startswith(p) for p in _BG_CACHE_PATHS)
    if is_bg_image:
        # ★ bg-image: 允许浏览器缓存 1 小时，ETag 驱动 304
        response.headers["Cache-Control"] = "private, max-age=3600"
        response.headers["Expires"] = "3600"
    elif path.startswith("/api/") or path.startswith("/static/"):
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    else:
        # ★ 动态 HTML 页面（除 API/static/bg-image 外的所有 GET 页面）：
        #   一律禁用浏览器缓存，强制每次重新渲染。
        #   以避免浏览器命中旧 HTML 整页缓存（如侧边栏折叠/展开状态等模板改动不生效）。
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    # ★ 安全修复：添加安全响应头
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains"
    )
    # CSP 作为 HTTP 头补充 meta 标签（defense-in-depth）
    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'"
        )
    return response


# ── ★ 数据库连接释放兜底：每个请求结束时释放该线程所有未归还的连接 ──
def _release_db_connections(exc=None):
    """Flask teardown：释放当前线程借出但未归还的数据库连接。
    兜底保护：即使路由忘记 close()，请求结束也会自动归还。
    """
    with guard("_release_db_connections: from src.core.db_manager import rele", log=_log):
        from src.core.db_manager import release_thread_connections

        release_thread_connections()


# ── API 请求耗时记录（Prometheus 指标） ──
def _metrics_before_request():
    import time as _time

    request._metrics_start = _time.time()


def _metrics_after_request(response):
    with guard("_metrics_after_request: start = getattr(request, '_metrics_s", log=_log):
        start = getattr(request, "_metrics_start", None)
        if start is not None:
            duration = time.time() - start
            from src.extensions.metrics_collector import metrics_collector

            endpoint = request.endpoint or request.path
            metrics_collector.observe_api_request(
                method=request.method,
                endpoint=endpoint,
                status_code=response.status_code,
                duration_sec=duration,
            )
    return response


# ── 登录守卫 + 滑动续期 + 任务保护 ──
# ★ 白名单：这些路径不需要登录
_PUBLIC_PATHS = {
    "/api/login",
    "/api/logout",
    "/api/current-user",
    "/api/can-access",
    "/api/status",
    "/api/bg-image",
    "/api/users",
    "/api/register",  # ★ v2: 公开自注册
    "/metrics",  # Prometheus 监控端点（无需登录）
}

# ★ API Token 白名单：这些路径前缀不需要项目上下文（webhook 回调等）
#   2026-08-29 [已拍板] 移除 "/api/run-all"：run-all 已禁用（用户铁律 Step1→5 逐步骤），
#   不再需要放行其 Token 免登录访问。
_API_TOKEN_WHITELIST = {
    "/api/step/",  # ★ /api/step/<n> 精确匹配，不误伤 /api/step4-*
    "/api/status",
    "/api/webhook",
}

# ★ 滑动过期：cookie 基本有效期 3 小时。
#    每次活动（API 请求）或任务在跑 → 自动续期。
#    只有连续 3 小时无活动 + 无任务 → 才过期。
SESSION_IDLE_MINUTES = 180  # 3 小时


def _require_login():
    """简洁但完整的登录守卫：
    1. 白名单路径：仍然先同步 session → thread-local（避免上下文污染），再放行
    2. session.user_id → thread-local 同步
    3. 已登录但项目丢失 → 自动恢复
    4. 已登录但 API key 丢失 → 自动恢复
    5. 未登录访问 /api/* → 返回 401
    6. 已登录 → 滑动续期
    """
    path = request.path.rstrip("/")

    # ★ API Token 鉴权：优先于 Session 鉴权（用于 CI/CD 外部调用）
    api_token = _extract_api_token()
    if api_token:
        # ★ 先检查路径白名单，再验证 token（避免非白名单路径触发无意义 DB 查询）
        token_allowed = any(path.startswith(p) for p in _API_TOKEN_WHITELIST)
        if token_allowed:
            with guard("_require_login: from src.backend._api_auth import ve", log=_log):
                from src.backend._api_auth import verify_api_token, has_api_token_scope

                token_info = verify_api_token(api_token)
                if token_info.get("ok") and has_api_token_scope(token_info, "run"):
                    # ★ Token 鉴权通过 → 设置 thread-local context 后放行
                    from src.core.config_loader import set_current_user

                    set_current_user(f"ci:{token_info.get('label', 'token')}")
                    # 设置项目上下文（优先 token 绑定的项目）
                    token_project = token_info.get("project", "*")
                    if token_project and token_project != "*":
                        from src.backend.state import _set_current_project

                        _set_current_project(token_project)
                        from src.core.config_loader import config as _cfg

                        with guard("_require_login: _cfg.reload(token_project)", log=_log, level=logging.WARNING):
                            _cfg.reload(token_project)
                    return None  # 鉴权通过，放行
        # Token 鉴权失败，继续走 Session 鉴权（不拦截，双通道兼容）

    # 2. 导入依赖（延迟导入，避免循环导入）
    try:
        from flask import session as _sess
        from src.core.config_loader import (
            get_current_user,
            set_current_user,
            get_session_key,
            set_session_key,
        )
        from src.backend.state import (
            _current_project,
            _set_current_project,
            _current_version,
            _set_current_version,
        )
    except Exception as e:
        _log.error("[_require_login] import failed: %s", str(e)[:200])
        if path.startswith("/api/"):
            return jsonify({"error": "服务器内部错误"}), 500
        return None

    # 3. 始终同步 session → thread-local（避免 worker 线程复用造成的 user_id 串扰）
    session_uid = _sess.get("user_id")
    if session_uid:
        set_current_user(session_uid)
    else:
        set_current_user(None)

    # ★ 3b. 同步 session.project_name → thread-local（避免 worker 线程复用造成的项目串扰）
    # 修复：admin 切换项目后，线程池复用可能导致 _current_project() 残留旧值，
    # 而 admin 能访问所有项目，旧值不会触发清理，导致 KPI 数据来自错误项目。
    # ★ 修复(2026-08-14): 新增 elif 分支 — session 无项目但 thread-local 有残留时，
    #   主动清除 thread-local，避免跨请求项目串扰（非 admin 用户可能看到 admin 的项目数据）。
    #   清除后由 4b 恢复逻辑自动分配第一个可用项目（仅已登录用户）。
    session_proj = _sess.get("project_name", "")
    current_proj = _current_project()
    if session_proj and session_proj != current_proj:
        try:
            from src.backend.helpers import _user_can_access_project

            if session_uid and _user_can_access_project(session_uid, session_proj):
                _set_current_project(session_proj)
                from src.core.config_loader import config as _cfg

                _cfg.reload(session_proj)
                from src.core.excel_handler import excel_handler

                excel_handler.reload_paths()
        except Exception as _e:
            _log.debug("session_project sync: %s", _e, exc_info=True)
    elif not session_proj and current_proj:
        # session 无项目但 thread-local 有残留 → 清除，由 4b 恢复
        _set_current_project("")

    # ★ 3c. 同步 session.version_label → thread-local（避免 worker 线程复用造成的版本串扰）
    # 与 3b 同理：_current_version() 存于 thread-local，线程池复用时不会自动清理。
    # ★ 修复(2026-08-14): 新增 elif 分支 — session 无版本但 thread-local 有残留时，
    #   主动清除，避免跨请求版本串扰。
    session_ver = _sess.get("version_label", "")
    current_ver = _current_version()
    if session_ver and session_ver != current_ver:
        try:
            _set_current_version(session_ver)
        except Exception as _e:
            _log.debug("session_version sync: %s", _e, exc_info=True)
    elif not session_ver and current_ver:
        # session 无版本但 thread-local 有残留 → 清除
        _set_current_version("")

    # 1. 白名单：无条件放行（已先做 session 同步）
    if path in _PUBLIC_PATHS:
        return None

    uid = get_current_user()

    # 4. 已登录用户的辅助操作
    if uid:
        # 4a. API key 丢失 → 自动恢复
        if not get_session_key():
            try:
                from src.users.manager import get_user_credential

                key = get_user_credential(uid, "API_KEY", project="*")
                if key:
                    set_session_key(key)
            except Exception as _e:
                _log.debug("session + import + session: %s", _e, exc_info=True)

        # 4b. 项目丢失 → 仅恢复用户上次显式选择的会话项目；不再自动分配第一个可用项目。
        #     ★ 根因(2026-08-31)：旧实现会在无会话项目时静默 auto-assign 排序后的第一个项目，
        #       导致「用户并未选择、系统却弹出一个项目」——KPI 总览/控制台显示的不是用户真正
        #       选的项目（且未执行项目显示空指标/误导性结论）。改为：只恢复显式存储的
        #       project_name；没有存储的会话项目时保持为空，由前端把用户引流到
        #       「项目管理/新建项目」必须先显式选择项目才能真正进入系统。
        if not _current_project():
            saved_project = _sess.get("project_name", "")
            if saved_project:
                try:
                    from src.backend.helpers import _user_can_access_project

                    if _user_can_access_project(uid, saved_project):
                        _set_current_project(saved_project)
                        from src.core.config_loader import config as _cfg

                        _cfg.reload(saved_project)
                        from src.core.excel_handler import excel_handler

                        excel_handler.reload_paths()
                except Exception as _e:
                    _log.debug("save + import + save: %s", _e, exc_info=True)

        # 4c. 滑动续期已由 _maybe_extend_session() 接管（before_request 钩子）

        return None  # 已登录，放行

    # 5. 未登录：拦截 /api/* 请求（GET/POST 等都拦截）
    if path.startswith("/api/"):
        return jsonify({"ok": False, "error": "请先登录"}), 401

    # 6. 未登录但访问非 API 路径（如 /、/index.html）→ 放行，前端自行处理
    return None


def _maybe_extend_session():
    """
    滑动续期策略（before_request 钩子，已在 app.py 注册）：
    1. SSE 日志流不触发续期（长连接不算活动）
    2. 有活跃任务 → 每次请求都续期（确保流水线执行期间 session 不过期）
    3. 无活跃任务 → 每 5 分钟续一次（避免每次请求写 MySQL 耗尽连接池）
    4. SESSION_IDLE_MINUTES 分钟无活动 + 无任务 → 自然过期
    """
    import time as _time

    # SSE 日志流长连接，不触发续期
    if request.path.startswith("/api/log-stream"):
        return

    now = _time.time()

    # 检查是否有活跃任务
    has_active_tasks = False
    try:
        from src.backend.state import _active_runs, _active_runs_lock

        with _active_runs_lock:
            has_active_tasks = len(_active_runs) > 0
    except Exception as _e:
        _log.debug("session + import: %s", _e, exc_info=True)

    # 决定是否续期
    should_extend = False
    if has_active_tasks:
        # ★ 有任务在跑 → 每次请求都续期，确保流水线执行期间 session 不会过期
        #    之前的"每 90 分钟续一次"逻辑有 bug：last_activity 每请求更新，
        #    seconds_idle 始终很小，导致 session 从不续期，3 小时后必然过期
        should_extend = True
    else:
        # ★ 无活跃任务 → 每 5 分钟续一次（避免每次请求写 MySQL 耗尽连接池）
        _SESSION_WRITE_INTERVAL = 300  # 5 分钟
        last_write = session.get("_last_session_write") or 0
        if now - last_write > _SESSION_WRITE_INTERVAL:
            should_extend = True

    if should_extend:
        session["last_activity"] = now
        session["_last_session_write"] = now
        session.modified = True  # Flask 重新签发 cookie，重置 max-age


def _extract_api_token() -> str:
    """从请求中提取 API Token（X-API-Key 头部优先，其次 Authorization Bearer）。"""
    token = request.headers.get("X-API-Key", "").strip()
    if not token:
        auth = request.headers.get("Authorization", "").strip()
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    return token or ""
