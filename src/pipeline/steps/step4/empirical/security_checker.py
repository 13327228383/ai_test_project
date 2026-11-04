# -*- coding: utf-8 -*-
"""安全（security）检查器 — step4 集成包装器。

将 ``src/extensions/security_scanner``（OWASP Top 10 规则驱动检测：SQL 注入 / XSS /
敏感信息泄露 / 安全头 / CSRF / 路径遍历 / 命令注入 / IDOR）接入 step4 流水线。
在 browser 模式用例执行完成后，对当前页面 HTML 运行扫描，产出结构化发现列表，
作为独立验证维度写入报告 —— 与 a11y/perf/visual 三横切门禁对齐，补全核心目标
「覆盖全面（…安全…）」的一等公民横切维度。

设计约束（与 a11y_checker.py 同构）：
  - 规则驱动、无 AI 依赖、通用（不认任何靶子业务逻辑）
  - 采集失败 → 返回带 error 的结果（fail-closed），不静默
  - 与 target_empirical 报告 schema 兼容，新增 ``security`` 顶层键
  - 受 SECURITY_SCAN_ENABLED 配置开关控制（SecurityScanner 内部默认开启）
"""

from __future__ import annotations

import logging
from typing import Any
from src.core.severity import SEVERITY_ORDER as _SEVERITY_ORDER  # noqa: F401  词表唯一源

_log = logging.getLogger(__name__)

# 靶子级 security 配置默认值
DEFAULT_SECURITY_CONFIG: dict[str, Any] = {
    "enabled": True,       # 默认开启（可在 target_cfg 中关闭）
    "source": "step4",     # 来源标识（写入报告）
    "max_findings": 100,   # 单页最大发现数（防刷屏）
}

# 【G3 消除 browser 安全盲区】HEAD-001（安全响应头缺失）依赖 HTTP 响应头：
#   - API 轨：scan_api_calls 自带 headers，天然覆盖；
#   - browser 轨：DOM 内无响应头，需由调用方（empirical/serial 等执行后端）把
#     页面顶层文档响应头以 `security.headers` 注入本检查器，才可评 HEAD-001。
#   - IDOR 为**跨身份 HTTP 对比**（scan_api_calls._detect_idor），纯前端 DOM 无意义，
#     属 API 轨职责，不在本 DOM 检查器做。此边界在文档与 serial/target_empirical 落库口径一致。



def run_security_check(page: Any, target_cfg: dict) -> dict[str, Any]:
    """在 browser 模式用例执行后，对当前页面运行安全扫描。

    参数：
        page: Playwright Page 对象（已导航）或兼容 ``content()`` 的静态快照。
        target_cfg: 靶子配置 dict（含可选的 ``security`` 段）。

    返回：
        dict: {enabled, source, total, by_severity, findings, error}。
        ``enabled=False`` 时返回空结果（不报错）。
        采集失败时 ``error`` 非空。
    """
    cfg = {**DEFAULT_SECURITY_CONFIG, **(target_cfg.get("security") or {})}
    if not cfg.get("enabled", True):
        return {
            "enabled": False,
            "source": cfg.get("source", "step4"),
            "total": 0,
            "by_severity": {},
            "findings": [],
            "error": "",
        }

    source = cfg.get("source", "step4")
    max_findings = int(cfg.get("max_findings", 100))

    if page is None:
        return {
            "enabled": True,
            "source": source,
            "total": 0,
            "by_severity": {},
            "findings": [],
            "error": "page 为空，无法采集 HTML",
        }

    # 1. 获取页面 HTML
    try:
        html = page.content()
    except Exception as exc:
        _log.warning("[security] page.content() 失败: %s", str(exc)[:120])
        return {
            "enabled": True,
            "source": source,
            "total": 0,
            "by_severity": {},
            "findings": [],
            "error": f"page.content() 失败: {str(exc)[:160]}",
        }

    if not html or not html.strip():
        return {
            "enabled": True,
            "source": source,
            "total": 0,
            "by_severity": {},
            "findings": [],
            "error": "页面 HTML 为空",
        }

    # 2. 调用通用安全扫描器
    try:
        from src.extensions.security_scanner import SecurityScanner

        # 【G3】浏览器轨 DOM 无响应头，HEAD-001 需调用方注入 `security.headers`；
        # 有则评安全头缺失，无则诚实跳过（不伪造 findings）。
        injected_headers = cfg.get("headers")
        has_headers = bool(injected_headers and isinstance(injected_headers, dict))
        scanner = SecurityScanner()
        scanner.scan_response(
            body=html,
            content_type="text/html",
            url=str(getattr(page, "url", "") or ""),
            status_code=200,
            headers=injected_headers if has_headers else None,
        )
        finding_dicts = scanner.to_dict_list()
    except Exception as exc:
        _log.error("[security] 安全扫描器异常: %s", exc, exc_info=True)
        return {
            "enabled": True,
            "source": source,
            "total": 0,
            "by_severity": {},
            "findings": [],
            "error": f"安全扫描器异常: {str(exc)[:160]}",
        }

    # 3. 截断（防刷屏）
    if len(finding_dicts) > max_findings:
        _log.info("[security] 发现 %d 条，截断到 %d 条", len(finding_dicts), max_findings)
        finding_dicts = finding_dicts[:max_findings]

    # 4. 统计严重度分布
    by_severity: dict[str, int] = {}
    for f in finding_dicts:
        sev = str(f.get("severity") or "low").lower()
        by_severity[sev] = by_severity.get(sev, 0) + 1

    return {
        "enabled": True,
        "source": source,
        "total": len(finding_dicts),
        "by_severity": by_severity,
        "findings": finding_dicts,
        "error": "",
    }


__all__ = ["run_security_check", "DEFAULT_SECURITY_CONFIG", "_SEVERITY_ORDER"]