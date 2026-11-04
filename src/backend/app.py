# -*- coding: utf-8 -*-
"""
E2E 智能测试引擎 — Web 控制面板 (Flask + SSE)
===============================================
入口文件（facade）：创建 Flask 应用，注册所有 Blueprint、中间件、错误处理器、SSE 路由。

架构（v3 拆分后）：
  src/backend/
    app.py                     ← 本文件（facade，创建 app + 注册，~200 行）
    _ring_buffer.py            ← RingBuffer 环形队列（纯 Python，无 Flask 依赖）
    _middleware.py             ← 中间件（缓存/连接释放/指标/登录守卫/滑动续期）
    _error_handlers.py         ← API JSON 错误处理器
    _sse_routes.py             ← SSE 日志流 + /metrics + 前端入口路由
    state.py                   ← 全局状态变量 + getter/setter（线程安全）
    log_capture.py             ← LogCapture + _broadcast_log + install_log_capture
    log_persistence.py         ← 运行日志持久化（磁盘写入）
    helpers.py                 ← 业务辅助函数
    executor.py                ← _run_step / _retry_failed / _clear_files
    observers.py               ← AI 观察者 + Router 日志劫持
    routes/                    ← 各路由 Blueprint
"""

import sys
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent  # src/backend/../../ = 项目根
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from src.core.guarded import guard

# ── 从子模块导入（绝对导入：兼容 `python src/backend/app.py` 脚本启动与模块导入）──
# RingBuffer re-export，保持 `from src.backend.app import RingBuffer` 可用
from src.backend._ring_buffer import RingBuffer

# 中间件
from src.backend._middleware import (
    _no_cache,
    _release_db_connections,
    _metrics_before_request,
    _metrics_after_request,
    _require_login,
    _maybe_extend_session,
    _PUBLIC_PATHS,
    SESSION_IDLE_MINUTES,
    try_api_cache,
    save_api_cache,
    invalidate_api_cache,
    invalidate_cache_on_write,
)

from src.backend.helpers import _user_can_access_project  # noqa: F401 — 中间件 before_request 中调用

# 错误处理器
from src.backend._error_handlers import (
    _api_error_response,
    _handle_400,
    _handle_401,
    _handle_403,
    _handle_404,
    _handle_405,
    _handle_500,
)

# SSE / 日志 / 前端路由
from src.backend._sse_routes import (
    prometheus_metrics,
    root_redirect,
    page_log_panel,
    log_panel_app,
    api_log_stream,
    api_log_history,
    api_log_history_run,
    api_log_runs,
    api_current_run_id,
    page_login,
    page_register,
    page_dashboard,
    page_pipeline,
    page_docs,
    page_charts,
    page_metrics_page,
    page_logs,
    page_quality_panel,
    page_action_lib,
    page_test_methods,
    page_project_mgmt,
    page_new_project,
    page_version_mgmt,
    page_user_mgmt,
    page_role_mgmt,
    page_change_password,
    page_config,
    page_system_config,
    page_prompt_templates,
    page_config_history,
    page_pipeline_config,
    page_skill_rules,
    page_step4_config,
    page_personal_config,
    page_file_upload,
    page_clear_files,
    page_intent_edit,
    page_intent_review,
    page_review,
    page_confidence_block,
    page_datasource,
    page_ds_api,
    page_ds_database,
    page_ds_design,
    page_ds_requirements,
    page_ds_target,
    page_ai_health,
    page_diagnostics,
    page_nav_tree,
    page_audit_logs,
    page_ci_integration,
    page_export_page,
    page_step3_case_upload,
    page_clarify,
    api_audit_logs,
)

from flask import Flask

# ── 创建 Flask 应用 ──
_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
app = Flask(
    __name__,
    template_folder=str(_frontend_dir / "templates"),
    static_folder=str(_frontend_dir / "static"),
)

# ★ Flask secret_key：优先环境变量，开发环境使用内置默认值
import os as _os

_DEV_SECRET_KEY = "ai_test_engine_dev_secret_v1_change_in_production"
_secret_key = _os.environ.get("FLASK_SECRET_KEY", _DEV_SECRET_KEY)
app.secret_key = (
    _secret_key.encode("utf-8") if isinstance(_secret_key, str) else _secret_key
)
if _os.environ.get("FLASK_SECRET_KEY"):
    _log.info("[Session] 使用环境变量 FLASK_SECRET_KEY")
else:
    _log.info("[Session] 使用内置开发密钥（生产环境请设置 FLASK_SECRET_KEY 环境变量）")

# ★ Flask-Session: 服务端 session 存储（解决浏览器刷新丢失登录态问题）
from datetime import timedelta

# ★ Cookie 安全属性（必须在 Session(app) 之前设置）
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = (
    "Lax"  # ★ Lax 确保同站请求携带 cookie（F5 刷新保留登录态）
)
# SESSION_COOKIE_SECURE：仅生产环境（HTTPS）启用，本地 HTTP 部署为 False
app.config["SESSION_COOKIE_SECURE"] = _os.environ.get("FLASK_ENV", "") == "production"
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 最大上传 50MB

# ★ Session 存储：MySQL（不走文件系统，不污染项目目录）
SESSION_STORAGE = _os.environ.get("SESSION_STORAGE", "mysql").lower()

if SESSION_STORAGE == "redis":
    app.config["SESSION_TYPE"] = "redis"
    app.config["SESSION_REDIS"] = None
    app.config["SESSION_KEY_PREFIX"] = "flask_session:"
    _log.info("[Session] 使用 Redis 存储 session")
elif SESSION_STORAGE == "filesystem":
    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_FILE_DIR"] = _os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)), "..", "..", "flask_session_files"
    )
    app.config["SESSION_FILE_THRESHOLD"] = 500
    _log.info("[Session] 使用文件系统存储 session")
else:
    # MySQL/SQLAlchemy（默认）
    app.config["SESSION_TYPE"] = "sqlalchemy"
    from src.core.config_loader import config as _cfg

    _db_host = _cfg._get("STORAGE_MYSQL_HOST")
    _db_port = int(_cfg._get("STORAGE_MYSQL_PORT"))
    _db_user = _cfg._get("STORAGE_MYSQL_USER")
    _db_pass = _cfg._get("STORAGE_MYSQL_PASSWORD")
    _db_name = _cfg._get("STORAGE_MYSQL_DATABASE")
    _db_charset = _cfg._get("STORAGE_MYSQL_CHARSET")
    _sqlalchemy_uri = f"mysql+pymysql://{_db_user}:{_db_pass}@{_db_host}:{_db_port}/{_db_name}?charset={_db_charset}"
    app.config["SQLALCHEMY_DATABASE_URI"] = _sqlalchemy_uri
    app.config["SESSION_SQLALCHEMY_TABLE"] = "flask_sessions"
    # Flask-Session SQLAlchemy 连接池配置（防止 MySQL Too many connections）
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_size": 2,
        "max_overflow": 2,
        "pool_recycle": 300,
    }
    from flask_sqlalchemy import SQLAlchemy

    _db = SQLAlchemy(app)
    app.config["SESSION_SQLALCHEMY"] = _db
    _log.info("[Session] 使用 MySQL 存储 session (表: flask_sessions)")

app.config["SESSION_PERMANENT"] = True

# ★ 滑动过期：cookie 基本有效期 3 小时。
#    每次活动（API 请求）或任务在跑 → 自动续期。
#    只有连续 3 小时无活动 + 无任务 → 才过期。
# SESSION_IDLE_MINUTES 由 _middleware 提供（=180）
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=SESSION_IDLE_MINUTES)

# ★ 初始化 Flask-Session（必须在所有配置之后）
from flask_session import Session

Session(app)

app.config["TEMPLATES_AUTO_RELOAD"] = True  # ★ 模板文件变更后自动重载（无需重启服务）

# ── 注册中间件（顺序与原 app.py 完全一致）──
app.after_request(_no_cache)
app.teardown_request(_release_db_connections)
app.before_request(_metrics_before_request)
app.after_request(_metrics_after_request)
# ★ 全局 GET API 缓存：before_request 最先检查缓存命中，after_request 最后写入缓存
# 必须在 _require_login 之前检查缓存（命中后直接返回，跳过后续所有处理）
app.before_request(try_api_cache)
# ★ 非 GET 请求清空全部 API 缓存（POST/PUT/DELETE 触发数据变更）
app.before_request(invalidate_cache_on_write)
app.before_request(_require_login)
app.before_request(
    _maybe_extend_session
)  # 滑动会话续期（有活跃任务 → 每 90 分钟续一次，无任务 → 每请求续一次）
# ★ 缓存写入：在所有 after_request 之后，确保拿到最终响应
app.after_request(save_api_cache)


# ── 初始化：日志捕获 + 观察者 + 路由劫持 ──
from src.backend.log_capture import install_log_capture
from src.backend.observers import (
    _register_ai_observers,
    _hijack_router_logs,
    update_monitor_dom,
    log_step_ok,
    log_step_fail,
)
from src.core.structured_logging import configure_structured_logging

configure_structured_logging()
install_log_capture()
_register_ai_observers()
_hijack_router_logs()

# 应用启动时预初始化数据库 schema，避免首次 API 请求承担初始化延迟
with guard("from src.core.db_manager import init_sch", log=_log, level=logging.WARNING):
    from src.core.db_manager import init_schema_on_startup

    init_schema_on_startup()

# ★ 2026-07-14: prompt_templates 种子初始化（DB 唯一事实源）
#   schema 初始化后，若 prompt_templates 表为空则写入种子模板
with guard("from src.core.prompt_templates import pr", log=_log, level=logging.WARNING):
    from src.core.prompt_templates import prompt_templates

    _seed_cnt = prompt_templates.seed_if_empty()
    if _seed_cnt > 0:
        _log.info("[Startup] prompt_templates 种子已写入: %d 条", _seed_cnt)

# ★ 2026-07-13: 预热 embedding 模型，避免首次 metrics 请求卡 14s
# _get_embedding_status() 会触发 _ensure_local_model() 从磁盘加载 SentenceTransformer，
# 不预热的话首次访问 /api/metrics-summary 会阻塞 14s+。
with guard("import threading as _threading", log=_log):
    import threading as _threading

    def _warmup_embedding():
        with guard("_warmup_embedding: from src.extensions.semantic_index i", log=_log):
            from src.extensions.semantic_index import get_model_status

            _ms = get_model_status()
            _log.info("[Startup] embedding 模型预热完成 (mode=%s)", _ms.get("mode"))

    _threading.Thread(target=_warmup_embedding, daemon=True).start()

with guard("from src.pipeline.steps.step4 import bro", log=_log, level=logging.WARNING):
    from src.pipeline.steps.step4 import browser_core

    browser_core.update_monitor_dom = update_monitor_dom
    browser_core.log_step_ok = log_step_ok
    browser_core.log_step_fail = log_step_fail
    _log.info("[Startup] browser_core monitor 注入成功")

# ★ 启动自检：检测 Playwright 是否可用（Step4 浏览器执行依赖）。
#   不抛异常、不阻断启动；失败仅给出安装指引。
with guard("import playwright", log=_log, level=logging.WARNING):
    import playwright  # noqa: F401

    _log.info("[Startup] BROWSER_READY: Playwright 已就绪")

# ★ 初始化全流水线插件系统（实例化 4 个插件并 subscribe 到 pipeline_bus）
with guard("from src.pipeline.plugins.bootstrap impo", log=_log, level=logging.WARNING):
    from src.pipeline.plugins.bootstrap import init_pipeline_plugins

    init_pipeline_plugins()


# ── 注册所有 Blueprint ──
from src.backend.routes.auth import auth_bp
from src.backend.routes.project import project_bp
from src.backend.routes.version import version_bp
from src.backend.routes.execution import exec_bp
from src.backend.routes.data import data_bp
import src.backend.routes.data_models  # ★ AI 模型选择路由（在子模块 import 时把 endpoints 注册到 data_models_bp）
import src.backend.routes.data_datasources  # ★ 数据源 & 需求审核 & 背景路由（挂在 data_bp 上，无独立 bp）
from src.backend.routes.docs import docs_bp
from src.backend.routes.docs_analytics import docs_analytics_bp
from src.backend.routes.health import health_bp
from src.backend.routes.chart_data import chart_bp
from src.backend.routes.action_library import action_library_bp
from src.backend.routes.case_rules import case_rules_bp  # ★ 用例生成规则 API
from src.backend.routes.synonym_routes import synonym_bp
from src.backend.routes.overrides_routes import (
    overrides_bp,
)  # ★ 项目级 Prompt/同义词覆盖
import src.backend.routes.execution.review  # ★ 需求审核路由（自注册）
import src.backend.routes.case_review  # ★ 测试用例审核路由（自注册）
import src.backend.routes.calibration  # ★ P1-7: 校准环路由（自注册）
import src.backend.routes.nav_match  # ★ v5.15: 导航匹配可观测路由（自注册）
import src.backend.routes.oracle  # ★ P1-7: 视觉 Oracle 路由（自注册）
import src.backend.routes.file_cleanup  # ★ 文件清理路由（自注册）
from src.backend.routes.file_upload import file_upload_bp  # ★ 文件上传路由
from src.backend.routes.export import export_bp  # ★ 导出路由
from src.backend.routes.pipeline_plugins import pipeline_plugins_bp  # ★ 流水线插件路由
from src.backend.routes.p0_plugins import p0_plugins_bp  # ★ P0 治理插件路由
from src.backend.routes.metrics_page import metrics_page_bp  # ★ 独立指标中心页面
from src.backend.routes.ci_integration import (
    ci_bp,
)  # ★ CI/CD 集成（API Token + Webhook + 模板）
from src.backend.routes.pipeline_config import pipeline_cfg_bp  # ★ 流水线用户配置
from src.backend.routes.system_config_api import system_cfg_bp  # ★ 系统级配置（跨项目）
from src.backend.routes.diagnostics.diagnostics_api import (
    diagnostics_bp,
)  # ★ 诊断中心（自检+健康快照+失败归因）
from src.backend.routes.quality import quality_bp  # ★ W3 质量治理只读聚合
from src.backend.routes.data_models import data_models_bp  # ★ AI 模型选择路由（/api/ai-defaults、/api/task-models 等）
from src.backend.routes.case_upload import case_upload_bp  # ★ 用例上传绑定（Step3 用例准备）
from src.backend.routes.clarification import clarification_bp  # ★ T3 流水线强化专项: 交互式需求澄清
from src.backend.routes.kpi import kpi_bp  # ★ T5 流水线强化专项: 统一 KPI 聚合

# ── 注册所有 Blueprint（带防重复注册保护）──
def _safe_register_blueprint(bp, **kwargs):
    """注册 Blueprint，跳过已注册的（pytest 多模块共享 app 时防重复）。"""
    if bp.name not in app.blueprints:
        # Flask 3.x: reset _got_registered_once to allow re-registration
        # (pytest 测试隔离需要：R2 测试会创建独立 app 注册 exec_bp)
        if hasattr(bp, '_got_registered_once'):
            bp._got_registered_once = False
        with guard("_safe_register_blueprint: app.register_blueprint(bp, **kwargs)", log=_log):
            app.register_blueprint(bp, **kwargs)

_safe_register_blueprint(auth_bp)
_safe_register_blueprint(project_bp)
_safe_register_blueprint(version_bp)
_safe_register_blueprint(exec_bp)
_safe_register_blueprint(data_bp)
_safe_register_blueprint(data_models_bp)
_safe_register_blueprint(docs_bp)
_safe_register_blueprint(docs_analytics_bp)
_safe_register_blueprint(health_bp)
_safe_register_blueprint(chart_bp)
_safe_register_blueprint(action_library_bp)
_safe_register_blueprint(case_rules_bp)
_safe_register_blueprint(synonym_bp)
_safe_register_blueprint(overrides_bp)
_safe_register_blueprint(file_upload_bp)
_safe_register_blueprint(export_bp)
_safe_register_blueprint(pipeline_plugins_bp)
_safe_register_blueprint(p0_plugins_bp)
_safe_register_blueprint(metrics_page_bp)
_safe_register_blueprint(diagnostics_bp)
_safe_register_blueprint(quality_bp)
_safe_register_blueprint(ci_bp)
_safe_register_blueprint(pipeline_cfg_bp)
_safe_register_blueprint(system_cfg_bp)
_safe_register_blueprint(case_upload_bp)
_safe_register_blueprint(clarification_bp)
_safe_register_blueprint(kpi_bp)


# ── P2-4: 静态资源自动指纹（替代模板手工 ?v= 版本号）──
#   用法：模板里写 {{ static_ver('app-common.js') }}，
#   返回 "/static/app-common.js?v=<mtime8hex>"，文件修改后指纹自动变化，
#   浏览器缓存自动失效，无需人工维护版本号。
#   log-panel（Vite 构建）产物本身已带内容哈希文件名，不受影响。
from hashlib import md5 as _md5

_static_ver_cache: dict = {}


def _static_ver(filename: str) -> str:
    """为静态文件生成带 mtime 指纹的 URL。文件不存在时退化为无版本 URL。"""
    try:
        key = filename.lstrip("/")
        cached = _static_ver_cache.get(key)
        if cached:
            return cached
        p = _frontend_dir / "static" / key.replace("/", "\\")
        if not p.is_file():
            p = _frontend_dir / "static" / key
        if not p.is_file():
            return "/static/" + key
        stat = p.stat()
        fp = f"{stat.st_mtime_ns:x}"
        ver = _md5(fp.encode()).hexdigest()[:8]
        url = "/static/" + key + "?v=" + ver
        _static_ver_cache[key] = url
        return url
    except Exception as _e:
        _log.debug("[static_ver] 生成失败 %s: %s", filename, _e)
        return "/static/" + filename.lstrip("/")


app.jinja_env.globals["static_ver"] = _static_ver


# ── 模型列表由 /api/task-models 等接口实时拉取（model_registry.fetch_provider_models），无需预热 ──


# ── 注册错误处理器（顺序与原 app.py 一致）──
app.errorhandler(400)(_handle_400)
app.errorhandler(401)(_handle_401)
app.errorhandler(403)(_handle_403)
app.errorhandler(404)(_handle_404)
app.errorhandler(405)(_handle_405)
app.errorhandler(500)(_handle_500)


# ── 注册路由（顺序与原 app.py 一致）──
app.route("/metrics")(prometheus_metrics)
app.route("/")(root_redirect)
# ★ 根因(2026-08-31)：React 日志面板 bundle 内建 basename="/log-panel"，只在 /log-panel 渲染。
#    故 /log-panel 原样承载 React 应用（供 /realtime-logs 主壳页 iframe 内嵌，保留侧边栏）。
app.route("/log-panel")(log_panel_app)
app.route("/realtime-logs")(page_log_panel)
app.route("/api/log-stream")(api_log_stream)
app.route("/api/log-history")(api_log_history)
app.route("/api/log-history/<run_id>")(api_log_history_run)
app.route("/api/log-runs")(api_log_runs)
app.route("/api/run-id")(api_current_run_id)

# ── 多页面架构：各业务页面路由 ──
app.route("/login")(page_login)
app.route("/register")(page_register)
app.route("/dashboard")(page_dashboard)
app.route("/pipeline")(page_pipeline)
app.route("/docs")(page_docs)
app.route("/charts")(page_charts)
app.route("/metrics-page")(page_metrics_page)
app.route("/logs")(page_logs)
app.route("/quality-panel")(page_quality_panel)  # ★ T04/P1-4：W3 质量治理面板
app.route("/action-lib")(page_action_lib)
app.route("/test-methods")(page_test_methods)
app.route("/project-mgmt")(page_project_mgmt)
app.route("/new-project")(page_new_project)
app.route("/version-mgmt")(page_version_mgmt)
app.route("/user-mgmt")(page_user_mgmt)
app.route("/role-mgmt")(page_role_mgmt)
app.route("/change-password")(page_change_password)
app.route("/personal-config")(page_personal_config)
app.route("/config")(page_config)
app.route("/system-config")(page_system_config)
app.route("/prompt-templates")(page_prompt_templates)
app.route("/config-history")(page_config_history)
app.route("/pipeline-config")(page_pipeline_config)
app.route("/skill-rules")(page_skill_rules)
app.route("/step4-config")(page_step4_config)

app.route("/file-upload")(page_file_upload)
app.route("/clear-files")(page_clear_files)
app.route("/intent-edit")(page_intent_edit)
app.route("/intent-review")(page_intent_review)
app.route("/review")(page_review)
app.route("/confidence-block")(page_confidence_block)
app.route("/datasource")(page_datasource)
app.route("/ds-api")(page_ds_api)
app.route("/ds-database")(page_ds_database)
app.route("/ds-design")(page_ds_design)
app.route("/ds-requirements")(page_ds_requirements)
app.route("/ds-target")(page_ds_target)
app.route("/ai-health")(page_ai_health)
app.route("/diagnostics")(page_diagnostics)
app.route("/nav-tree")(page_nav_tree)
app.route("/audit-logs")(page_audit_logs)
app.route("/api/audit-logs")(api_audit_logs)
app.route("/ci-integration")(page_ci_integration)
app.route("/export-page")(page_export_page)
app.route("/step3-case-upload")(page_step3_case_upload)
app.route("/clarify")(page_clarify)  # ★ T3 流水线强化专项: 需求澄清问答页


# ── ★ 2026-08-12: 系统级版本号 API ──
def api_version():
    """返回引擎版本号信息。"""
    from src.version import get_version_info
    from flask import jsonify
    return jsonify(get_version_info())


app.route("/api/version")(api_version)


# ── 启动入口 ──
def main():
    import argparse

    parser = argparse.ArgumentParser(description="E2E 智能测试引擎 Web 控制面板")
    # ★ P0-04: host 默认从 config.web.bind_host 读取(默认 127.0.0.1，限制攻击面)
    try:
        from src.core.config_loader import config as _cfg

        _default_host = _cfg._get("WEB_BIND_HOST", "127.0.0.1") or "127.0.0.1"
    except Exception:
        _default_host = _os.environ.get("WEB_BIND_HOST", "127.0.0.1")
    parser.add_argument("--host", default=_default_host, help="绑定地址")
    parser.add_argument("--port", type=int, default=8700, help="绑定端口(默认 8700)")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    args = parser.parse_args()
    # ★ P0-03: 启动时执行 bootstrap 流程 (检测/初始化 admin 用户 + 重命名演示项目)
    try:
        from src.users.bootstrap import run_all as _run_bootstrap

        _bootstrap_result = _run_bootstrap()
        if _bootstrap_result.get("admin"):
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "[Bootstrap] admin 用户已初始化（密码已设置，详见启动环境变量或配置文件）",
            )
    except Exception as _e:
        import logging as _logging

        _logging.getLogger(__name__).warning("[Bootstrap] 跳过: %s", _e)

    # ★ 使用 waitress 生产级 WSGI 服务器（替代 Flask 开发服务器，避免不稳定崩溃）
    # ★ 方案C: 从 16 线程扩展到 48 线程，支持多用户多项目并发（5+ 用户同时执行流水线）
    import sys as _sys
    from waitress import serve as _waitress_serve

    _WAITRESS_THREADS = 48

    # ★ 修复：crash 日志同时写入文件，避免终端关闭后丢失退出原因
    from pathlib import Path as _Path
    from datetime import datetime as _dt

    _log_dir = _Path(__file__).resolve().parent.parent.parent / "logs"
    _log_dir.mkdir(exist_ok=True)
    _crash_log_path = _log_dir / "server_crash.log"

    def _write_crash_log(msg: str):
        """将退出/崩溃信息写入文件 + stderr，确保终端关闭时也有记录"""
        _ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        _full = f"[{_ts}] {msg}"
        try:
            with open(_crash_log_path, "a", encoding="utf-8") as _f:
                _f.write(_full + "\n")
        except Exception as _e:
            _log.debug("[app] 写入崩溃日志文件异常: %s", _e, exc_info=True)
        try:
            _sys.stderr.write(_full + "\n")
            _sys.stderr.flush()
        except Exception as _e:
            _log.debug("[app] 写入 stderr 异常: %s", _e, exc_info=True)

    # ★ 修复：注册 Windows 控制台信号处理，捕获窗口关闭/CTRL_BREAK 等事件
    try:
        import signal as _signal

        def _on_signal(signum, _frame):
            _sig_map = {
                2: "SIGINT(Ctrl+C)",
                15: "SIGTERM",
                21: "CTRL_BREAK",
                5: "CTRL_CLOSE",
                6: "CTRL_LOGOFF",
                5: "CTRL_SHUTDOWN",
            }
            _write_crash_log(
                f"[SHUTDOWN] 收到信号 {_sig_map.get(signum, signum)}，进程即将退出"
            )

        for _sig in (_signal.SIGINT, _signal.SIGTERM):
            _signal.signal(_sig, _on_signal)
        if hasattr(_signal, "SIGBREAK"):
            _signal.signal(_signal.SIGBREAK, _on_signal)
    except Exception as _e:
        _write_crash_log(f"[WARN] 信号处理注册失败: {_e}")

    # ★ 僵尸检查点清理：启动时清理 status=running 但无活跃内存任务的检查点
    #   根因: 后端崩溃/强制杀死时，pipeline_checkpoints 中 running 状态无法更新
    #         → 下次启动时流水线假阻塞（上次会话阻塞约 30 分钟）
    #   通用性: 适用于所有项目，所有 step
    #   降级: 清理失败不阻断启动
    try:
        from src.pipeline.utils.checkpoint_store import checkpoint_store as _cp_store

        _zombie_count = _cp_store.cleanup_zombie_checkpoints()
        if _zombie_count > 0:
            _write_crash_log(f"[SERVER] 僵尸检查点已清理: {_zombie_count} 条")
    except Exception as _zombie_e:
        _write_crash_log(f"[SERVER] 僵尸检查点清理跳过(非阻断): {_zombie_e}")

    _write_crash_log(
        f"[SERVER] waitress 启动: {args.host}:{args.port} (threads={_WAITRESS_THREADS})"
    )
    try:
        # ★ 修复 IPv6 兼容：localhost 解析优先 ::1，若只监听 0.0.0.0 (IPv4)，
        #   客户端连 ::1 失败要等 TCP 超时（~2s）才回退 IPv4，导致每个请求慢 2 秒。
        #   用 listen 参数同时监听 IPv4 + IPv6，消除 IPv6 回退延迟。
        _listen_spec = f"{args.host}:{args.port}"
        if args.host in ("0.0.0.0", ""):
            _listen_spec = f"0.0.0.0:{args.port} [::]:{args.port}"
        _waitress_serve(app, listen=_listen_spec, threads=_WAITRESS_THREADS)
    except KeyboardInterrupt:
        _write_crash_log("[SHUTDOWN] Ctrl+C 收到，正在退出...")
    except SystemExit as _se:
        # ★ 修复：SystemExit 继承自 BaseException，不会被 except Exception 捕获
        _write_crash_log(
            f"[CRASH] SystemExit({_se.code}) — 某处调用了 sys.exit()，进程退出"
        )
    except BaseException as _be:
        # ★ 修复：捕获所有未处理异常（包括 BaseException 子类），确保退出有日志
        import traceback as _tb

        _write_crash_log(
            f"[CRASH] waitress 未处理异常 ({type(_be).__name__}): {_be}\n{_tb.format_exc()}"
        )
        # ★ 尝试优雅关闭
        try:
            from src.backend.log_capture import _sse_force_stop

            _sse_force_stop.set()
        except Exception as _e:
            _log.debug("[app] SSE 强制停止异常: %s", _e, exc_info=True)
        _sys.exit(1)


if __name__ == "__main__":
    main()
