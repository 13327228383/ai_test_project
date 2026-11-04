# -*- coding: utf-8 -*-
"""
API 前缀自动探测器
==================

问题：YApi 文档记录的 API 路径是相对路径（如 /login），前端通过 baseURL 自动加前缀
（如 /api）。引擎直调时如果不加前缀，会打到 nginx 静态资源返回 405/HTML。

方案：3 级 fallback 自动探测，无需人工配置，适配任何前端框架和缝合怪系统。

1. 读前端 JS 的 baseURL（最准）：请求首页 → 提取 JS 文件 → 正则匹配 baseURL
2. 探测常见前缀（兜底）：对 target 路径依次试 /api /prod-api /dev-api 等，看哪个返回 JSON
3. 缓存探测结果（性能）：首次探测后缓存到 source_documents 表，后续直接读缓存

缝合怪系统适配：按 target 路径分组探测，每个 target 独立缓存自己的前缀。
"""
import logging
from src.core.guarded import guard
import re
import json
import time
import urllib.request
import urllib.error
from typing import Optional, Dict, Tuple
from urllib.parse import urlparse

_log = logging.getLogger(__name__)

# 常见 API 前缀（按出现频率排序）
_COMMON_PREFIXES = [
    "/api",
    "/prod-api",
    "/dev-api",
    "/test-api",
    "/backend",
    "/server",
    "/service",
    "/rest",
    "/v1",
    "/v2",
]

# 内存缓存（进程级，避免同进程内重复探测）
_MEMORY_CACHE: Dict[str, Tuple[str, float]] = {}  # key: base_url → (prefix, cached_ts)
_CACHE_TTL = 3600  # 内存缓存 1 小时


def _fetch_html_and_js(base_url: str, timeout: int = 8) -> Tuple[str, list]:
    """请求首页 HTML，提取 JS 文件列表。

    Returns:
        (html_content, js_urls) — js_urls 为绝对 URL 列表
    """
    try:
        r = urllib.request.urlopen(base_url + "/", timeout=timeout)
        html = r.read().decode("utf-8", "ignore")
    except Exception as e:
        _log.debug("[api_prefix] 获取首页失败: %s", str(e)[:80])
        return "", []

    # 提取 JS 文件引用（src="xxx.js" 或 src='/xxx.js'）
    js_paths = re.findall(r'src=["\']([^"\']+\.js)["\']', html)
    # 转绝对 URL
    parsed = urlparse(base_url)
    origin = "%s://%s" % (parsed.scheme, parsed.netloc)
    js_urls = []
    for p in js_paths:
        if p.startswith("http"):
            js_urls.append(p)
        elif p.startswith("/"):
            js_urls.append(origin + p)
        else:
            js_urls.append(origin + "/" + p)
    return html, js_urls


def _detect_from_js(base_url: str, js_urls: list, timeout: int = 8) -> Optional[str]:
    """从 JS 文件中正则匹配 baseURL 配置。

    支持的格式（Vue/React/Angular 通用）：
        baseURL: "/api"
        baseURL: '/api'
        baseURL:"/api"
        axios.create({baseURL:"/api"})
        VITE_API_BASE_URL = "/api"
        const API_PREFIX = "/api"
    """
    # 正则：匹配 baseURL / VITE_API / API_PREFIX 等常见变量名
    patterns = [
        r'baseURL\s*[:=]\s*["\']([^"\']+)["\']',  # baseURL:"/api"
        r'VITE_\w*API\w*\s*[:=]\s*["\']([^"\']+)["\']',  # VITE_API_BASE_URL="/api"
        r'VUE_APP_\w*API\w*\s*[:=]\s*["\']([^"\']+)["\']',  # VUE_APP_API_BASE="/api"
        r'API_PREFIX\s*[:=]\s*["\']([^"\']+)["\']',  # API_PREFIX="/api"
        r'API_BASE\s*[:=]\s*["\']([^"\']+)["\']',  # API_BASE="/api"
        r'apiBase\s*[:=]\s*["\']([^"\']+)["\']',  # apiBase:"/api" (Angular)
    ]
    # 最多检查前 5 个 JS 文件（避免全量扫描太慢）
    for js_url in js_urls[:5]:
        try:
            r = urllib.request.urlopen(js_url, timeout=timeout)
            js = r.read().decode("utf-8", "ignore")
        except Exception as _fe:
            _log.debug("[api_prefix] JS拉取失败(跳过): %s", str(_fe)[:50])
            continue
        for pat in patterns:
            matches = re.findall(pat, js)
            for m in matches:
                # 过滤：必须是路径格式（以 / 开头），排除空值和完整 URL
                if m and m.startswith("/") and len(m) <= 30:
                    _log.info(
                        "[api_prefix] 从 JS %s 检测到 baseURL: %s",
                        js_url.split("/")[-1][:30],
                        m,
                    )
                    return m
    return None


def _probe_prefix(
    base_url: str, target_path: str, method: str, timeout: int = 8
) -> Optional[str]:
    """探测常见前缀，返回第一个让 target 返回 JSON 的前缀。

    判定标准：HTTP 200 + Content-Type 含 json，或 body 以 { 开头。
    405/404/HTML 都视为错误前缀。
    """
    parsed = urlparse(base_url)
    origin = "%s://%s" % (parsed.scheme, parsed.netloc)
    target_path = target_path.lstrip("/")

    for prefix in _COMMON_PREFIXES:
        url = "%s/%s/%s" % (origin.rstrip("/"), prefix.strip("/"), target_path)
        try:
            data = b"{}" if method in ("POST", "PUT", "DELETE") else None
            req = urllib.request.Request(
                url,
                data=data,
                method=method,
                headers={"Content-Type": "application/json"},
            )
            r = urllib.request.urlopen(req, timeout=timeout)
            ct = r.headers.get("Content-Type", "")
            body = r.read()[:200].decode("utf-8", "ignore")
            # JSON 判定：Content-Type 含 json，或 body 以 { 开头
            if "json" in ct.lower() or body.strip().startswith("{"):
                _log.info(
                    "[api_prefix] 探测命中: %s %s → 200 JSON (前缀=%s)",
                    method,
                    target_path,
                    prefix,
                )
                return prefix
        except urllib.error.HTTPError as e:
            # 401/403 也说明 API 存在（只是需要认证），视为命中
            if e.code in (401, 403):
                ct = e.headers.get("Content-Type", "")
                body = e.read()[:200].decode("utf-8", "ignore") if e.fp else ""
                if "json" in ct.lower() or body.strip().startswith("{"):
                    _log.info(
                        "[api_prefix] 探测命中(需认证): %s %s → %s JSON (前缀=%s)",
                        method,
                        target_path,
                        e.code,
                        prefix,
                    )
                    return prefix
            # 405/404/500 继续试下一个前缀
        except Exception as _pe:
            _log.debug("[api_prefix] 前缀探测失败(跳过): %s", str(_pe)[:50])
            continue
    return None


def _load_from_db_cache(project: str, base_url: str) -> Optional[str]:
    """从 source_documents 表读取缓存的 API 前缀。"""
    with guard("_load_from_db_cache: from src.core.db_manager import get_", log=_log):
        from src.core.db_manager import get_connection, release_connection
        from src.core.versioning import current_version_label

        vlabel = current_version_label(project) or ""
        conn = None
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT content FROM source_documents "
                "WHERE project=%s AND doc_type='api_prefix_cache' AND version_label=%s "
                "LIMIT 1",
                (project, vlabel),
            ).fetchone()
            if row and row.get("content"):
                data = json.loads(row["content"])
                # 缓存按 base_url 隔离（缝合怪系统可能换 base_url）
                cached = data.get(base_url, {})
                prefix = cached.get("prefix", "")
                if prefix:
                    _log.debug(
                        "[api_prefix] DB 缓存命中: project=%s base=%s prefix=%s",
                        project,
                        base_url,
                        prefix,
                    )
                    return prefix
        finally:
            if conn:
                release_connection(conn)
    return None


def _save_to_db_cache(project: str, base_url: str, prefix: str) -> None:
    """将探测结果缓存到 source_documents 表。"""
    with guard("_save_to_db_cache: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
        from src.core.db_manager import get_connection, release_connection
        from src.core.versioning import current_version_label

        vlabel = current_version_label(project) or ""
        conn = None
        try:
            conn = get_connection()
            # 读取现有缓存（按主键 project+doc_type+version_label 查询）
            row = conn.execute(
                "SELECT content FROM source_documents "
                "WHERE project=%s AND doc_type='api_prefix_cache' AND version_label=%s "
                "LIMIT 1",
                (project, vlabel),
            ).fetchone()
            if row:
                data = json.loads(row.get("content") or "{}")
            else:
                data = {}
            # 更新该 base_url 的前缀
            data[base_url] = {"prefix": prefix, "detected_at": time.time()}
            # upsert：主键 (project, doc_type, version_label) 存在则更新，不存在则插入
            conn.execute(
                "INSERT INTO source_documents(project, version_label, doc_type, content, updated_at) "
                "VALUES(%s, %s, 'api_prefix_cache', %s, NOW()) "
                "ON DUPLICATE KEY UPDATE content=VALUES(content), updated_at=NOW()",
                (project, vlabel, json.dumps(data, ensure_ascii=False)),
            )
            conn.commit()
            _log.info(
                "[api_prefix] DB 缓存已保存: project=%s base=%s prefix=%s",
                project,
                base_url,
                prefix,
            )
        finally:
            if conn:
                release_connection(conn)


def resolve_api_prefix(
    base_url: str, target_path: str = "/login", method: str = "GET", project: str = ""
) -> str:
    """自动探测目标系统的 API 前缀。

    3 级 fallback：
      1. 内存缓存（同进程内）
      2. DB 缓存（跨进程）
      3. 读前端 JS baseURL（最准）
      4. 探测常见前缀（兜底）

    Args:
        base_url: 目标系统根 URL（如 http://<target-host>:<port>）
        target_path: 用于探测的 API 路径（默认 /login）
        method: 探测用的 HTTP method（默认 GET）
        project: 项目名（用于 DB 缓存隔离）

    Returns:
        API 前缀字符串（如 "/api"），无前缀时返回 ""
    """
    if not base_url:
        return ""

    # 1. 内存缓存
    cached = _MEMORY_CACHE.get(base_url)
    if cached and (time.time() - cached[1] < _CACHE_TTL):
        return cached[0]

    # 2. DB 缓存
    if project:
        db_prefix = _load_from_db_cache(project, base_url)
        if db_prefix is not None:
            _MEMORY_CACHE[base_url] = (db_prefix, time.time())
            return db_prefix

    # 3. 读前端 JS baseURL
    _log.info("[api_prefix] 开始自动探测: base=%s target=%s", base_url, target_path)
    with guard("resolve_api_prefix: _, js_urls = _fetch_html_and_js(base", log=_log):
        _, js_urls = _fetch_html_and_js(base_url)
        if js_urls:
            js_prefix = _detect_from_js(base_url, js_urls)
            if js_prefix:
                _MEMORY_CACHE[base_url] = (js_prefix, time.time())
                if project:
                    _save_to_db_cache(project, base_url, js_prefix)
                return js_prefix

    # 4. 探测常见前缀
    probed_prefix = _probe_prefix(base_url, target_path, method)
    if probed_prefix:
        _MEMORY_CACHE[base_url] = (probed_prefix, time.time())
        if project:
            _save_to_db_cache(project, base_url, probed_prefix)
        return probed_prefix

    # 5. 全部失败：无前缀（target 即完整路径）
    _log.warning("[api_prefix] 自动探测失败，使用无前缀: base=%s", base_url)
    _MEMORY_CACHE[base_url] = ("", time.time())
    return ""


def build_url_with_auto_prefix(
    base_url: str, target: str, project: str = "", method: str = "GET"
) -> str:
    """拼接完整 URL，自动探测并补全 API 前缀。

    Args:
        base_url: 目标系统根 URL
        target: API 路径（可能是相对路径如 /login，或完整 URL）
        project: 项目名（用于缓存）
        method: HTTP method（用于探测）

    Returns:
        完整 URL，探测失败或 target 已是完整 URL 时直接返回
    """
    if not target or target.strip().lower() in ("unknown", ""):
        return ""
    target = target.strip()

    # target 可能是 "POST /login" 格式，只取 path 部分
    parts = target.split(None, 1)
    if len(parts) == 2 and parts[0].upper() in (
        "GET",
        "POST",
        "PUT",
        "DELETE",
        "PATCH",
    ):
        target = parts[1].strip()

    if target.startswith("http://") or target.startswith("https://"):
        return target
    if not base_url:
        return ""

    # base_url 可能含路径，只取 scheme+host+port
    parsed = urlparse(base_url)
    if parsed.path and parsed.path != "/":
        base_url = "%s://%s" % (parsed.scheme, parsed.netloc)

    # 自动探测 API 前缀
    api_prefix = resolve_api_prefix(
        base_url, target_path=target, method=method, project=project
    )

    # 拼接（避免 target 已含 prefix 时重复）
    if api_prefix and not target.startswith(api_prefix):
        target = api_prefix.rstrip("/") + "/" + target.lstrip("/")

    return "%s/%s" % (base_url.rstrip("/"), target.lstrip("/"))
