# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Figma 元数据实时检查（headless 浏览器，不消耗 REST API 配额）
"""

import json
import threading
import time
from pathlib import Path
from typing import Any
import logging

_log = logging.getLogger(__name__)

from src.pipeline.steps.step1.figma_cache import FigmaCache
from ._auto_login import _auto_login_figma, _get_meta_profile

# =============================================================================
# Figma 元数据实时检查
# =============================================================================

_META_CHECK_LOCK = threading.Lock()
_meta_last_result: dict[str, Any] | None = None
_meta_last_check: float = 0.0
_META_CACHE_TTL = 120  # 元数据缓存 2 分钟，避免频繁开浏览器


def check_figma_modified_time(file_key: str = None) -> dict[str, Any]:
    """
    使用 headless 浏览器访问 Figma 网页版元数据 API，获取设计稿的最新修改时间
    （零配额）。

    API: www.figma.com/api/files/{fileKey}
    认证: 浏览器 session cookie（不走 REST API PAT，不消耗配额）
    浏览器: headless 模式，静默运行

    注意: Web API 仅返回文件级元数据 (meta.updated_at)，不包含 document 树，
          无法检测页面级的结构变更或内容变更。

    返回:
        {"success": bool, "design_updated_at": str, "cache_last_modified": str,
         "is_cache_fresh": bool, "needs_login": bool, "error": str}
    """
    global _meta_last_result, _meta_last_check

    now = time.time()
    # 缓存最近一次检查结果（2 分钟内不重复开浏览器）
    with _META_CHECK_LOCK:
        if _meta_last_result is not None and (now - _meta_last_check) < _META_CACHE_TTL:
            return dict(_meta_last_result)

    if not file_key:
        from src.core.config_loader import config

        file_key = config.figma_file_key
    if not file_key:
        return {"success": False, "error": "file_key 未配置", "needs_login": False}

    # 获取缓存中的 lastModified 做对比
    from src.core.paths import ProjectPaths as _PP3
    from src.core.config_loader import config as _cfg

    db_path = _PP3(_cfg.project_root).raw_figma_cache_db
    cache = FigmaCache(db_path)
    cached_full = cache.get_full_file(file_key)
    cache_modified = cached_full.get("last_modified", "") if cached_full else ""

    result = _fetch_meta_via_headless_browser(file_key, cache_modified)

    with _META_CHECK_LOCK:
        _meta_last_result = dict(result)
        _meta_last_check = time.time()

    return result


def fetch_figma_nodes(file_key: str = None, node_ids: list = None, depth: int = 2):
    """模块级便捷方法：查询 MySQL 缓存的 Figma 节点。

    Args:
        file_key: Figma 文件 key；None 时自动读取 config.figma_file_key
        node_ids: 节点 ID 列表；None 则查询该 file_key 下所有节点缓存节点
        depth: 深度；默认 2
    返回: Dict，结构同 FigmaCache.fetch_figma_nodes（nodes={}, missing=[]）。
    """
    from src.core.paths import ProjectPaths as _PP4
    from src.core.config_loader import config as _cfg2

    _db_path = _PP4(_cfg2.project_root).raw_figma_cache_db
    cache = FigmaCache(_db_path)
    return cache.fetch_figma_nodes(file_key=file_key, node_ids=node_ids, depth=depth)


def _fetch_meta_via_headless_browser(
    file_key: str, cache_modified: str
) -> dict[str, Any]:
    """通过 headless Playwright 访问 Figma 元数据 API，失败则自动登录重试。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "success": False,
            "error": "Playwright 未安装",
            "needs_login": False,
            "cache_last_modified": cache_modified,
        }

    meta_url = f"https://www.figma.com/api/files/{file_key}"
    result = {
        "success": False,
        "error": "",
        "needs_login": False,
        "design_updated_at": "",
        "cache_last_modified": cache_modified,
        "is_cache_fresh": False,
    }

    try:
        # ★ _meta_check.py 在 figma_cache/ 下，比 playwright_engine/ 多一层目录
        chrome_path = str(
            Path(__file__).resolve().parent.parent.parent.parent.parent.parent
            / "chrome-win64"
            / "chrome.exe"
        )
        if not Path(chrome_path).is_file():
            chrome_path = None

        launch_opts = {
            "headless": True,
            "args": [
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
            "no_viewport": True,
            "locale": "zh-CN",
        }
        if chrome_path:
            launch_opts["executable_path"] = chrome_path

        # 尝试 1: 用已有 cookie 访问
        if _try_fetch_meta(
            str(_get_meta_profile()), meta_url, launch_opts, result, cache_modified
        ):
            return result

        # 尝试 2: 自动登录后重试
        if _auto_login_figma():
            if _try_fetch_meta(
                str(_get_meta_profile()), meta_url, launch_opts, result, cache_modified
            ):
                return result

        result["needs_login"] = True
        result["error"] = "自动登录失败，请检查账号密码"
        return result

    except Exception as e:
        result["error"] = str(e)[:120]
        return result


def _try_fetch_meta(
    profile_dir: str, url: str, launch_opts: dict, result: dict, cache_modified: str
) -> bool:
    """尝试用浏览器 fetch 获取 Figma 元数据。返回 True 表示成功。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir, **launch_opts
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            # 先访问一个 figma 轻量页面建立 origin（让 fetch 带 figma.com cookie）
            try:
                page.goto(
                    "https://www.figma.com/robots.txt",
                    wait_until="domcontentloaded",
                    timeout=8000,
                )
            except Exception as _e:
                _log.debug("[_meta_check] _try_fetch_meta 异常: %s", _e, exc_info=True)

            # 使用 evaluate + fetch 避免浏览器下载弹窗
            raw = page.evaluate(
                """async (url) => {
                try {
                    const resp = await fetch(url, {credentials: 'include'});
                    const text = await resp.text();
                    return JSON.stringify({_ok: resp.ok, _status: resp.status, _body: text});
                } catch(e) {
                    return JSON.stringify({_error: e.message});
                }
            }""",
                url,
            )

            if not raw:
                return False

            data = json.loads(raw)
            if data.get("_error"):
                _log.error(f"  [Meta] fetch 错误: {data['_error']}")
                return False
            if not data.get("_ok"):
                return False

            body = json.loads(data.get("_body", "{}"))
            if body.get("meta", {}).get("updated_at"):
                _parse_meta_response(body, result, cache_modified)
                return True

        finally:
            ctx.close()
    return False


def _parse_meta_response(
    data: dict, result: dict, cache_modified: str
) -> dict[str, Any]:
    """解析 Figma 元数据 API 响应并对比缓存。
    Web API 仅返回文件级元数据 (meta.updated_at)，不包含 document 树。"""
    meta = data.get("meta", {})
    design_updated = meta.get("updated_at", "")
    result["success"] = True
    result["design_updated_at"] = design_updated

    if cache_modified and design_updated:
        try:
            dt_design = _parse_iso(design_updated)
            dt_cache = _parse_iso(cache_modified)
            result["is_cache_fresh"] = dt_cache >= dt_design
            result["cache_age_vs_design"] = (
                dt_cache.timestamp() - dt_design.timestamp()
            ) / 3600
        except Exception as _e:
            _log.debug("[_meta_check] _parse_meta_response 异常: %s", _e, exc_info=True)

    return result


def _parse_iso(ts_str: str):
    """解析 ISO 时间字符串。"""
    from datetime import datetime

    ts_str = ts_str.replace("Z", "+00:00")
    return datetime.fromisoformat(ts_str)
