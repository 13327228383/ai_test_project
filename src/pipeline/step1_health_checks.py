# -*- coding: utf-8 -*-
"""Step1 数据源健康检查模块。

从 step1.py 拆分出的独立模块级函数，用于构建数据源健康检查结果。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)

# ── 常量 ──
_TRUNC_STATUS = 100  # sources_status detail 截断长度


def build_source_health_checks(
    config, probe_database: bool = True
) -> dict[str, dict[str, Any]]:
    """构建 Step1 数据源健康检查结果。
    ★ 支持 file 模式：本地文件路径模式不需要用户名/密码/token。
    """
    checks: dict[str, dict[str, Any]] = {}
    cf_pages = list(getattr(config, "confluence_page_ids", []) or [])
    if not cf_pages:
        checks["confluence"] = {"status": "skipped", "detail": "未配置 Confluence 页面"}
    else:
        mode = getattr(config, "confluence_mode", "mcp")
        missing = []
        if mode == "file":
            # file 模式：检查本地文件路径是否存在
            cf_url = str(getattr(config, "confluence_url", "") or "").strip()
            if not cf_url:
                missing.append("CONFLUENCE_URL（本地文件路径）")
            else:
                from pathlib import Path

                # 支持逗号分隔多个文件
                _all_exist = True
                for p in cf_url.split(","):
                    p = p.strip()
                    if p and not Path(p).exists():
                        _all_exist = False
                        break
                if not _all_exist:
                    checks["confluence"] = {
                        "status": "warning",
                        "detail": "file 模式: 部分文件路径不存在，执行时将尝试解析",
                    }
                else:
                    checks["confluence"] = {
                        "status": "ok",
                        "detail": "file 模式: %d 个文件待解析" % len(cf_pages),
                    }
        elif mode == "mcp":
            if not getattr(config, "confluence_pat", ""):
                missing.append("CONFLUENCE_PAT / CONFLUENCE_PASSWORD")
        else:
            if not getattr(config, "confluence_username", ""):
                missing.append("CONFLUENCE_USERNAME")
            if not getattr(config, "confluence_password", ""):
                missing.append("CONFLUENCE_PASSWORD")
        if missing:
            checks["confluence"] = {
                "status": "failed",
                "detail": "缺少 " + ", ".join(missing),
            }
    if not getattr(config, "figma_url", ""):
        checks["figma"] = {"status": "skipped", "detail": "未配置 Figma URL"}
    else:
        fig_url = str(getattr(config, "figma_url", "") or "").strip()
        # 本地文件路径模式不需要 token
        if not fig_url.startswith("http"):
            checks["figma"] = {"status": "ok", "detail": "本地文件模式: 路径已配置"}
        elif not hasattr(config, "figma_key") or not config.figma_key:
            # figma_key 通过 _user_cred 从 user_credentials_v2 读取，是属性而非 _config_data 键
            checks["figma"] = {"status": "failed", "detail": "缺少 FIGMA_KEY"}
        else:
            checks["figma"] = {"status": "ok", "detail": "Figma URL 与 Token 已配置"}
    if not getattr(config, "api_docs_enabled", False):
        checks["yapi"] = {"status": "skipped", "detail": "未启用 YApi"}
    else:
        api_url = str(getattr(config, "api_docs_base_url", "") or "").strip()
        # 本地文件路径模式不需要账号密码
        if api_url and not api_url.startswith("http"):
            checks["yapi"] = {"status": "ok", "detail": "本地文件模式: 路径已配置"}
        else:
            missing = []
            for attr, label in (
                ("api_docs_url", "API_DOCS_URL"),
                ("api_docs_username", "API_DOCS_USERNAME"),
                ("api_docs_password", "API_DOCS_PASSWORD"),
            ):
                if not getattr(config, attr, ""):
                    missing.append(label)
            checks["yapi"] = (
                {"status": "failed", "detail": "缺少 " + ", ".join(missing)}
                if missing
                else {
                    "status": "ok",
                    "detail": "YApi 配置完整，拉取阶段校验 API 配额/权限",
                }
            )
    if not getattr(config, "db_enabled", False):
        checks["database"] = {"status": "skipped", "detail": "未启用数据库"}
    else:
        db_type = str(getattr(config, "db_type", "") or "").lower()
        if db_type == "sqlite":
            # sqlite 只需要 TARGET_DB_NAME（文件路径）
            db_name = str(getattr(config, "db_name", "") or "").strip()
            if not db_name:
                checks["database"] = {
                    "status": "failed",
                    "detail": "缺少 TARGET_DB_NAME（SQLite 文件路径）",
                }
            else:
                from pathlib import Path

                if Path(db_name).exists():
                    checks["database"] = {
                        "status": "ok",
                        "detail": "SQLite 数据库文件已就绪",
                    }
                else:
                    checks["database"] = {
                        "status": "warning",
                        "detail": "SQLite 文件不存在，执行时将尝试创建",
                    }
        else:
            missing = []
            for attr, label in (
                ("db_host", "TARGET_DB_HOST"),
                ("db_user", "DB_USER"),
                ("db_password", "DB_PASSWORD"),
                ("db_name", "TARGET_DB_NAME"),
            ):
                if not getattr(config, attr, ""):
                    missing.append(label)
            if missing:
                checks["database"] = {
                    "status": "failed",
                    "detail": "缺少 " + ", ".join(missing),
                }
            elif not probe_database:
                checks["database"] = {"status": "ok", "detail": "数据库配置完整"}
            else:
                _sys_db_conn = None
                try:
                    from src.core.db_manager import get_connection

                    conn = get_connection()
                    try:
                        conn.execute("SELECT 1")
                        checks["database"] = {"status": "ok", "detail": "数据库可达"}
                    finally:
                        try:
                            conn.close()
                        except Exception as _e:
                            _log.debug(
                                "[step1_health_checks] 非关键异常: %s",
                                _e,
                                exc_info=True,
                            )
                except Exception as exc:
                    checks["database"] = {
                        "status": "failed",
                        "detail": "数据库不可达: %s" % str(exc)[:_TRUNC_STATUS],
                    }
    return checks


def _health_warnings(health_checks: dict[str, dict[str, Any]]) -> list[str]:
    return [
        "%s 健康检查失败: %s" % (source, info.get("detail", ""))
        for source, info in (health_checks or {}).items()
        if info.get("status") == "failed"
    ]
