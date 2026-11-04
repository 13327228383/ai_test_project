# -*- coding: utf-8 -*-
"""
Preflight 预检路由
==================
从 execution.py 拆分，注册到 exec_bp Blueprint。
"""

import time
from src.core.guarded import guard
import json
import logging
import threading as _threading
from flask import request, jsonify

_log = logging.getLogger(__name__)

from src.backend.routes.execution import (
    exec_bp,
    _check_step_permission,
    _current_project,
    _CACHE_STALE_DAYS,
)
from src.core.config_loader import config

# ★ 2026-07-13: target-system-status 结果缓存（30 秒 TTL，按项目隔离）
# 避免每次切换控制台都触发 8-30s HTTP 探测；状态展示不需要秒级实时
_target_status_cache = {}  # {project: (timestamp, response_dict)}
_target_status_cache_lock = _threading.Lock()  # ★ 防并发竞态
_TARGET_STATUS_TTL = 30  # 秒


@exec_bp.route("/api/preflight", methods=["GET"])
def api_preflight():
    """
    启动前预检：返回各数据源配置状态和 Figma 配额信息。
    前端在 Step 1 启动前调用此 API，弹窗展示摘要，用户确认后再执行。
    """
    from src.core.config_loader import config

    proj = request.args.get("project") or _current_project()
    if not proj:
        return jsonify({"ok": False, "error": "未选择项目"}), 400

    from src.backend.routes.execution import _check_step_permission

    ok, err = _check_step_permission(1, proj)
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )

    # 确保配置已加载
    try:
        config.reload(proj)
    except Exception as e:
        return jsonify({"ok": False, "error": "项目配置加载失败: %s" % str(e)}), 500

    sources = {}
    warnings_list = []

    # ── Confluence ──
    cf_page_ids = config.confluence_page_ids
    cf_configured = bool(cf_page_ids)
    if cf_configured:
        mode = config.confluence_mode
        if mode == "mcp":
            has_pat = bool(config.confluence_pat)
            if has_pat:
                sources["confluence"] = {
                    "status": "configured",
                    "detail": "%d 页 (MCP 模式)" % len(cf_page_ids),
                }
            else:
                sources["confluence"] = {
                    "status": "missing_credentials",
                    "detail": "缺少 CONFLUENCE_PASSWORD (PAT)",
                }
                warnings_list.append("Confluence 缺少 PAT，将跳过")
        else:
            has_user = bool(config.confluence_username)
            has_pass = bool(config.confluence_password)
            if has_user and has_pass:
                sources["confluence"] = {
                    "status": "configured",
                    "detail": "%d 页 (REST 模式)" % len(cf_page_ids),
                }
            else:
                missing_parts = []
                if not has_user:
                    missing_parts.append("USERNAME")
                if not has_pass:
                    missing_parts.append("PASSWORD")
                sources["confluence"] = {
                    "status": "missing_credentials",
                    "detail": "缺少 %s" % ", ".join(missing_parts),
                }
                warnings_list.append("Confluence 缺少凭据，将跳过")
    else:
        sources["confluence"] = {
            "status": "not_configured",
            "detail": "未配置 Confluence 页面（CONFLUENCE_PAGE_IDS 或 pageId 参数）",
        }
        warnings_list.append("Confluence 页面未配置，将跳过")

    # ── Figma ──
    figma_url = config.figma_url
    figma_configured = bool(figma_url)
    if figma_configured:
        has_key = bool(config.figma_key)
        if has_key:
            # 检查缓存状态
            cache_info = {"has_cache": False}
            try:
                from src.pipeline.steps.step1.figma_cache import get_figma_cache_info

                cache_info = get_figma_cache_info()
            except Exception as e:
                _log.warning("[preflight] Figma 缓存信息获取失败: %s", e)
                cache_info = {"has_cache": False}

            # 检查配额
            quota_info = {}
            with guard("api_preflight: from src.pipeline.steps.step1.figma_", log=_log, level=logging.WARNING):
                from src.pipeline.steps.step1.figma_quota import quota_tracker

                quota_info = quota_tracker.get_status()

            detail_parts = []
            if cache_info.get("has_cache"):
                detail_parts.append("缓存可用")
            else:
                detail_parts.append("缓存为空")
            if quota_info.get("plan_type") == "paid":
                detail_parts.append("付费账号(无月度限制)")
            else:
                remaining = quota_info.get("remaining", 0)
                detail_parts.append("免费账号(剩余 %d/月)" % remaining)

            sources["figma"] = {
                "status": "configured",
                "detail": ", ".join(detail_parts),
                "cache_available": cache_info.get("has_cache", False),
                "quota": quota_info,
            }
        else:
            sources["figma"] = {
                "status": "missing_credentials",
                "detail": "缺少 FIGMA_KEY (Personal Access Token)",
            }
            warnings_list.append("Figma 缺少 API Token，将跳过")
    else:
        sources["figma"] = {"status": "not_configured", "detail": "未配置 FIGMA_URL"}
        warnings_list.append("Figma 未配置，将跳过")

    # ── YApi ──
    yapi_configured = config.api_docs_enabled
    if yapi_configured:
        has_user = bool(config.api_docs_username)
        has_pass = bool(config.api_docs_password)
        if has_user and has_pass:
            # 检查缓存
            cache_info = ""
            try:
                from src.core.db_manager import get_connection

                _proj = getattr(config, "project_name", "") or ""
                _conn = get_connection()
                try:
                    _row = _conn.execute(
                        "SELECT updated_at FROM source_documents "
                        "WHERE project=%s AND doc_type='yapi' ORDER BY updated_at DESC LIMIT 1",
                        (_proj,),
                    ).fetchone()
                finally:
                    try:
                        _conn.close()
                    except Exception as _pass_e:
                        _log.warning("非关键异常: %s", _pass_e, exc_info=True)
                if _row and _row["updated_at"]:
                    _ut = _row["updated_at"]
                    _ts = (
                        _ut.timestamp()
                        if hasattr(_ut, "timestamp")
                        else time.mktime(_ut.timetuple())
                    )
                    age_min = (time.time() - _ts) / 60
                    cache_info = "缓存 %.0fmin前" % age_min
                else:
                    cache_info = "无缓存"
            except Exception as e:
                _log.warning("[preflight] YApi 缓存信息获取失败: %s", e)
                cache_info = ""
            sources["yapi"] = {
                "status": "configured",
                "detail": "已配置" + (", %s" % cache_info if cache_info else ""),
            }
        else:
            missing_parts = []
            if not has_user:
                missing_parts.append("USERNAME")
            if not has_pass:
                missing_parts.append("PASSWORD")
            sources["yapi"] = {
                "status": "missing_credentials",
                "detail": "缺少 %s" % ", ".join(missing_parts),
            }
            warnings_list.append("YApi 缺少凭据，将跳过")
    else:
        sources["yapi"] = {"status": "not_configured", "detail": "未配置 API_DOCS_URL"}

    # ── 数据库 ──
    target_db_type = str(config.db_type or "").strip()
    target_db_file = str(config.db_file or "").strip()
    target_db_host = str(config.db_host or "").strip()
    target_db_port = config.db_port
    target_db_name = str(config.db_name or "").strip()
    db_configured = config.db_enabled
    if db_configured:
        is_sqlite = target_db_type.lower() == "sqlite"
        has_file = bool(target_db_file)
        has_host = bool(target_db_host)
        has_user = bool(config.db_user)
        has_pass = bool(config.db_password)
        has_name = bool(target_db_name)
        all_have = (
            has_file if is_sqlite else (has_host and has_user and has_pass and has_name)
        )
        if all_have:
            sources["database"] = {
                "status": "configured",
                "detail": (
                    ("sqlite://%s" % target_db_file)
                    if is_sqlite
                    else (
                        "%s@%s:%s/%s"
                        % (
                            config.db_user,
                            target_db_host,
                            target_db_port,
                            target_db_name,
                        )
                    )
                ),
                "scope": "target_system",
            }
        else:
            missing_parts = []
            if is_sqlite and not has_file:
                missing_parts.append("FILE")
            if (not is_sqlite) and not has_host:
                missing_parts.append("HOST")
            if (not is_sqlite) and not has_user:
                missing_parts.append("USER")
            if (not is_sqlite) and not has_pass:
                missing_parts.append("PASSWORD")
            if (not is_sqlite) and not has_name:
                missing_parts.append("NAME")
            sources["database"] = {
                "status": "missing_credentials",
                "detail": "缺少 %s" % ", ".join(missing_parts),
            }
            warnings_list.append("数据库缺少凭据，将跳过")
    else:
        sources["database"] = {
            "status": "not_configured",
            "detail": "未配置数据库连接信息",
        }

    # ── DOM 爬取（依赖目标系统 URL + 登录凭据）──
    # ★ fix: 补全 dom_crawl 检测，避免前端显示"未检测"
    target_url = str(getattr(config, "target_url", "") or "").strip()
    target_username = str(getattr(config, "target_username", "") or "").strip()
    target_password = str(getattr(config, "target_password", "") or "").strip()
    if target_url:
        if target_username and target_password:
            sources["dom_crawl"] = {
                "status": "configured",
                "detail": "目标系统已配置 (%s)" % target_url,
            }
        else:
            missing_parts = []
            if not target_username:
                missing_parts.append("USERNAME")
            if not target_password:
                missing_parts.append("PASSWORD")
            sources["dom_crawl"] = {
                "status": "missing_credentials",
                "detail": "缺少 %s" % ", ".join(missing_parts),
            }
            warnings_list.append("DOM 爬取缺少登录凭据，将跳过")
    else:
        sources["dom_crawl"] = {
            "status": "not_configured",
            "detail": "未配置 TARGET_SYSTEM_URL",
        }

    # ── 缓存过时检查（MySQL source_documents updated_at）──
    cache_stale = False
    with guard("api_preflight: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
        from src.core.db_manager import get_connection
        from src.core.config_loader import config as _cfg

        _proj = getattr(_cfg, "project_name", "") or ""
        now = time.time()
        _conn = get_connection()
        try:
            for _dt in ("confluence", "yapi"):
                _row = _conn.execute(
                    "SELECT updated_at FROM source_documents "
                    "WHERE project=%s AND doc_type=%s ORDER BY updated_at DESC LIMIT 1",
                    (_proj, _dt),
                ).fetchone()
                if _row and _row["updated_at"]:
                    _ut = _row["updated_at"]
                    _ts = (
                        _ut.timestamp()
                        if hasattr(_ut, "timestamp")
                        else time.mktime(_ut.timetuple())
                    )
                    if (now - _ts) > _CACHE_STALE_DAYS * 86400:
                        cache_stale = True
                        if _dt == "confluence":
                            warnings_list.append(
                                "⚠️ 需求数据缓存已超过 5 天，建议勾选「强制刷新」获取最新数据"
                            )
        finally:
            try:
                _conn.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)

    # ── 上传文件（按当前版本统计，读取走 upload_documents 唯一入口）──
    from src.core.upload_documents import fetch_uploaded_documents
    from src.users.project_auth import get_active_version_label

    _proj = getattr(config, "project_name", "") or ""
    upload_count = len(
        fetch_uploaded_documents(_proj, get_active_version_label(_proj))
    )

    if upload_count > 0:
        sources["upload"] = {
            "status": "configured",
            "detail": "%d 份上传文档" % upload_count,
        }
    else:
        sources["upload"] = {"status": "not_configured", "detail": "无上传文档"}

    # ── 汇总 ──
    configured_count = sum(1 for s in sources.values() if s["status"] == "configured")
    missing_cred_count = sum(
        1 for s in sources.values() if s["status"] == "missing_credentials"
    )
    not_configured_count = sum(
        1 for s in sources.values() if s["status"] == "not_configured"
    )

    # ★ 数据源必须至少配置一个（含上传文件），否则无法启动 Step 1
    can_proceed = configured_count > 0

    # ★ 2026-07-17: 为已配置的5个数据源添加默认选择状态（供前端勾选）
    #    已配置且缓存存在 → 默认勾选（会重新拉取）；未配置 → 禁用
    #    用户取消勾选 → 跳过该数据源，前端提示手动上传功能清单
    # 可勾选的拉取源（dom_crawl 只是目标系统配置状态，不作为 skip_sources 拉取项）
    selection_options = {}
    for key in ["confluence", "figma", "yapi", "database"]:
        if key in sources:
            s = sources[key]
            selection_options[key] = {
                "label": {
                    "confluence": "Confluence 需求",
                    "figma": "Figma 设计",
                    "yapi": "YApi 接口",
                    "database": "数据库",
                    "dom_crawl": "DOM 爬取",
                }.get(key, key),
                "enabled": s["status"] == "configured",
                "checked": s["status"] == "configured",  # 默认全部勾选已配置的
            }

    return jsonify(
        {
            "ok": True,
            "project": proj,
            "can_proceed": can_proceed,
            "sources": sources,
            "selection_options": selection_options,
            "warnings": warnings_list,
            "cache_stale": cache_stale,
            "summary": {
                "configured": configured_count,
                "missing_credentials": missing_cred_count,
                "not_configured": not_configured_count,
                "total": len(sources),
            },
        }
    )


@exec_bp.route("/api/preflight/verify", methods=["POST"])
def api_preflight_verify():
    """
    数据源连通性验证：对已配置的数据源进行实际连通性测试。
    前端在预检弹窗中提供"验证连通性"按钮，点击后调用此 API。
    返回每个数据源的实际连通状态。
    """
    import urllib.request as _ur
    import urllib.error as _ue
    import ssl as _ssl

    proj = request.args.get("project") or _current_project()
    if not proj:
        from flask import jsonify as _jf

        return _jf({"ok": False, "error": "未选择项目"}), 400

    try:
        config.reload(proj)
    except Exception as e:
        return jsonify({"ok": False, "error": "项目配置加载失败: %s" % str(e)}), 500

    results = {}
    _ssl_ctx = _ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = _ssl.CERT_NONE

    def _quick_http(url, timeout=5):
        """快速 HTTP 连通性测试"""
        try:
            req = _ur.Request(url, method="HEAD")
            resp = _ur.urlopen(req, timeout=timeout, context=_ssl_ctx)
            return True, "HTTP %d" % resp.status
        except _ue.HTTPError as e:
            return True, "HTTP %d (响应正常)" % e.code
        except Exception as e:
            return False, str(e)[:80]

    # ── Confluence 连通性 ──
    if config.confluence_page_ids:
        cf_url = str(getattr(config, "confluence_url", "") or "").strip()
        mode = getattr(config, "confluence_mode", "mcp")
        if mode == "file":
            from pathlib import Path

            exists = Path(cf_url).exists() if cf_url else False
            results["confluence"] = {
                "status": "ok" if exists else "failed",
                "detail": "文件存在" if exists else "文件不存在: %s" % cf_url,
            }
        else:
            if cf_url:
                ok, detail = _quick_http(cf_url)
                results["confluence"] = {
                    "status": "ok" if ok else "failed",
                    "detail": detail,
                }
            else:
                results["confluence"] = {"status": "failed", "detail": "URL 为空"}
    else:
        results["confluence"] = {"status": "not_configured", "detail": "未配置"}

    # ── Figma 连通性 ──
    figma_url = str(getattr(config, "figma_url", "") or "").strip()
    if figma_url and config.figma_key:
        try:
            req = _ur.Request(
                "https://api.figma.com/v1/me",
                headers={"X-Figma-Token": config.figma_key},
            )
            resp = _ur.urlopen(req, timeout=10, context=_ssl_ctx)
            import json as _json

            data = _json.loads(resp.read())
            email = data.get("email", "未知")
            results["figma"] = {"status": "ok", "detail": "Token 有效 (%s)" % email}
        except _ue.HTTPError as e:
            results["figma"] = {
                "status": "failed",
                "detail": "Token 无效 (HTTP %d)" % e.code,
            }
        except Exception as e:
            results["figma"] = {"status": "failed", "detail": str(e)[:80]}
    elif figma_url:
        results["figma"] = {"status": "failed", "detail": "缺少 FIGMA_KEY"}
    else:
        results["figma"] = {"status": "not_configured", "detail": "未配置"}

    # ── YApi 连通性 ──
    if config.api_docs_enabled:
        api_url = str(getattr(config, "api_docs_base_url", "") or "").strip()
        if api_url:
            ok, detail = _quick_http(api_url)
            results["yapi"] = {"status": "ok" if ok else "failed", "detail": detail}
        else:
            results["yapi"] = {"status": "failed", "detail": "URL 为空"}
    else:
        results["yapi"] = {"status": "not_configured", "detail": "未配置"}

    # ── 数据库连通性 ──
    if config.db_enabled:
        try:
            from src.extensions.db.db_agent import DatabaseAgent

            agent = DatabaseAgent()
            agent.connect()
            agent.execute("SELECT 1")
            agent.close()
            results["database"] = {
                "status": "ok",
                "detail": "%s@%s:%s/%s 可达"
                % (config.db_user, config.db_host, config.db_port, config.db_name),
            }
        except Exception as e:
            results["database"] = {"status": "failed", "detail": str(e)[:80]}
    else:
        results["database"] = {"status": "not_configured", "detail": "未配置"}

    # ── DOM/靶子系统连通性 ──
    target_url = str(getattr(config, "target_url", "") or "").strip()
    if target_url:
        ok, detail = _quick_http(target_url)
        results["dom_crawl"] = {"status": "ok" if ok else "failed", "detail": detail}
    else:
        results["dom_crawl"] = {"status": "not_configured", "detail": "未配置"}

    # 汇总
    failed_configured = [name for name, r in results.items() if r["status"] == "failed"]
    all_ok = len(failed_configured) == 0
    configured_count = sum(
        1 for r in results.values() if r["status"] != "not_configured"
    )

    return jsonify(
        {
            "ok": True,
            "project": proj,
            "results": results,
            "all_ok": all_ok,
            "failed_configured": failed_configured,
            "summary": {
                "configured": configured_count,
                "ok": sum(1 for r in results.values() if r["status"] == "ok"),
                "failed": len(failed_configured),
                "not_configured": sum(
                    1 for r in results.values() if r["status"] == "not_configured"
                ),
            },
        }
    )


@exec_bp.route("/api/target-system-status", methods=["GET"])
def api_target_system_status():
    """
    目标系统连通性检查：验证 target_url 可达 + 登录凭据是否有效。
    用于控制台数据源状态实时展示。
    """
    import urllib.request as _ur
    import urllib.error as _ue
    import urllib.parse as _up
    import ssl as _ssl

    proj = request.args.get("project") or _current_project()
    if not proj:
        return jsonify({"ok": False, "error": "未选择项目"}), 400

    # ★ 2026-07-13: 命中缓存直接返回（避免每次切换控制台都 8-30s HTTP 探测）
    with _target_status_cache_lock:
        _cached = _target_status_cache.get(proj)
        if _cached and (time.time() - _cached[0]) < _TARGET_STATUS_TTL:
            return jsonify(_cached[1])

    try:
        config.reload(proj)
    except Exception as e:
        return jsonify({"ok": False, "error": "项目配置加载失败: %s" % str(e)}), 500

    _ssl_ctx = _ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = _ssl.CERT_NONE

    target_url = str(getattr(config, "target_url", "") or "").strip()
    target_username = str(getattr(config, "target_username", "") or "").strip()
    target_password = str(getattr(config, "target_password", "") or "").strip()

    # 未配置 URL
    if not target_url:
        return jsonify(
            {
                "configured": False,
                "reachable": False,
                "login_checked": False,
                "status": "not_configured",
                "detail": "未配置 TARGET_URL",
                "target_url": "",
            }
        )

    # 检查 HTTP 连通性
    reachable = False
    http_status = ""
    try:
        req = _ur.Request(target_url, method="GET")
        resp = _ur.urlopen(req, timeout=8, context=_ssl_ctx)
        reachable = True
        http_status = "HTTP %d" % resp.status
        _page_body = resp.read().decode("utf-8", errors="ignore")[:4096]
    except _ue.HTTPError as e:
        reachable = True
        http_status = "HTTP %d" % e.code
        _page_body = e.read().decode("utf-8", errors="ignore")[:4096] if e.fp else ""
    except Exception as e:
        http_status = str(e)[:80]
        _page_body = ""

    # 登录凭据检查
    has_credentials = bool(target_username and target_password)
    login_ok = False
    login_detail = ""

    if reachable and has_credentials:
        # 尝试登录：POST 到常见登录端点
        try:
            # 构建登录 URL（尝试 target_url + /login）
            _base = target_url.rstrip("/")
            _login_urls = [
                _base + "/login",
                _base + "/api/login",
                _base + "/auth/login",
            ]
            _login_data = _up.urlencode(
                {
                    "username": target_username,
                    "password": target_password,
                }
            ).encode("utf-8")

            _login_ok = False
            _login_err = ""
            for _login_url in _login_urls:
                try:
                    _req = _ur.Request(_login_url, data=_login_data, method="POST")
                    _req.add_header("Content-Type", "application/x-www-form-urlencoded")
                    _resp = _ur.urlopen(_req, timeout=10, context=_ssl_ctx)
                    _body = _resp.read().decode("utf-8", errors="ignore")[:2048]
                    # 登录成功判定：非 401/403，且不包含登录页面关键词
                    _login_keywords = [
                        "登录",
                        "login",
                        "密码",
                        "password",
                        "验证码",
                        "captcha",
                        "请先登录",
                    ]
                    _is_login_page = any(
                        kw.lower() in _body.lower() for kw in _login_keywords
                    )
                    if not _is_login_page:
                        _login_ok = True
                        login_detail = "登录成功 (%s)" % _login_url.rsplit("/", 1)[-1]
                        break
                except _ue.HTTPError as _he:
                    _login_err = "HTTP %d" % _he.code
                except Exception as _ex:
                    _login_err = str(_ex)[:60]

            if _login_ok:
                login_ok = True
            elif _login_err:
                login_detail = "登录尝试失败: %s" % _login_err
            else:
                login_detail = "未找到可用的登录端点"
        except Exception as _e:
            login_detail = "登录检查异常: %s" % str(_e)[:80]
    elif reachable and not has_credentials:
        login_detail = "未配置登录凭据"
    elif not reachable:
        login_detail = "目标系统不可达，无法验证登录"

    # 状态判定
    if not reachable:
        status = "unreachable"
        detail = http_status
    elif login_ok:
        status = "ok"
        detail = login_detail
    elif has_credentials:
        status = "login_failed"
        detail = login_detail or http_status
    else:
        status = "no_credentials"
        detail = "%s（未配置登录凭据）" % http_status

    _resp = {
        "configured": True,
        "reachable": reachable,
        "login_checked": reachable and has_credentials,
        "login_ok": login_ok,
        "has_credentials": has_credentials,
        "status": status,
        "detail": detail,
        "target_url": target_url,
        "http_status": http_status,
    }
    # ★ 写入缓存（30 秒 TTL，按项目隔离）
    with _target_status_cache_lock:
        _target_status_cache[proj] = (time.time(), _resp)
    return jsonify(_resp)
