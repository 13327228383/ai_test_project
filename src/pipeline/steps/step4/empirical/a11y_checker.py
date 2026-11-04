# -*- coding: utf-8 -*-
"""可访问性（a11y）检查器 — step4 集成包装器。

将 ``src/extensions/a11y/checker.py`` 的规则驱动检查能力接入 step4 流水线。
在 browser 模式用例执行完成后，获取当前页面 HTML 并运行 8 条通用规则，
产出结构化发现列表，作为独立验证维度写入报告。

设计约束：
  - 规则驱动、无 AI 依赖、通用（不认任何靶子业务逻辑）
  - 采集失败 → 返回带 error 的结果（fail-closed），不静默
  - 与 target_empirical 报告 schema 兼容，新增 ``a11y`` 顶层键
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

# 靶子级 a11y 配置默认值
DEFAULT_A11Y_CONFIG: dict[str, Any] = {
    "enabled": True,       # 默认开启（可在 target_cfg 中关闭）
    "source": "step4",     # 来源标识（写入报告）
    "max_findings": 100,   # 单页最大发现数（防刷屏）
}


def run_a11y_check(page: Any, target_cfg: dict) -> dict[str, Any]:
    """在 browser 模式用例执行后，对当前页面运行 a11y 检查。

    参数：
        page: Playwright Page 对象（已导航到目标页面）。
        target_cfg: 靶子配置 dict（含可选的 ``a11y`` 段）。

    返回：
        dict: {enabled, source, total, by_severity, findings, error}。
        ``enabled=False`` 时返回空结果（不报错）。
        采集失败时 ``error`` 非空。
    """
    a11y_cfg = {**DEFAULT_A11Y_CONFIG, **(target_cfg.get("a11y") or {})}
    if not a11y_cfg.get("enabled", True):
        return {
            "enabled": False,
            "source": a11y_cfg.get("source", "step4"),
            "total": 0,
            "by_severity": {},
            "findings": [],
            "error": "",
        }

    source = a11y_cfg.get("source", "step4")
    max_findings = int(a11y_cfg.get("max_findings", 100))

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
        _log.warning("[a11y] page.content() 失败: %s", str(exc)[:120])
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

    # 2. 调用通用检查器
    try:
        from src.extensions.a11y.checker import run_a11y_check as _check

        result = _check(html, source=source)
    except Exception as exc:
        _log.error("[a11y] 检查器异常: %s", exc, exc_info=True)
        return {
            "enabled": True,
            "source": source,
            "total": 0,
            "by_severity": {},
            "findings": [],
            "error": f"检查器异常: {str(exc)[:160]}",
        }

    # 3. 截断（防刷屏）
    findings = result.get("findings") or []
    if len(findings) > max_findings:
        _log.info("[a11y] 发现 %d 条，截断到 %d 条", len(findings), max_findings)
        findings = findings[:max_findings]

    return {
        "enabled": True,
        "source": result.get("source", source),
        "total": len(findings),
        "by_severity": result.get("by_severity", {}),
        "findings": findings,
        "error": "",
    }


def _run_rules_on_html(html: str, source: str, max_findings: int) -> dict[str, Any]:
    """对已取得的页面 HTML 运行规则检查（同步/异步两条路径共用）。"""
    if not html or not html.strip():
        return {
            "enabled": True, "source": source, "total": 0,
            "by_severity": {}, "findings": [], "error": "页面 HTML 为空",
        }
    try:
        from src.extensions.a11y.checker import run_a11y_check as _check

        result = _check(html, source=source)
    except Exception as exc:
        _log.error("[a11y] 检查器异常: %s", exc, exc_info=True)
        return {
            "enabled": True, "source": source, "total": 0,
            "by_severity": {}, "findings": [],
            "error": f"检查器异常: {str(exc)[:160]}",
        }
    findings = result.get("findings") or []
    if len(findings) > max_findings:
        findings = findings[:max_findings]
    return {
        "enabled": True,
        "source": result.get("source", source),
        "total": len(findings),
        "by_severity": result.get("by_severity", {}),
        "findings": findings,
        "error": "",
    }


async def run_a11y_check_async(page: Any, target_cfg: dict) -> dict[str, Any]:
    """异步执行路径的 a11y 门禁。

    背景：引擎存在两套浏览器执行栈——主流水线用同步 Playwright（本模块同步版
    page.content() 可直接取 HTML），而 PlaywrightEngine 是异步栈，同步版在此路径上
    page.content() 返回协程对象，后续 .strip() 必然抛
    "'coroutine' object has no attribute 'strip'"，被 fail-safe 吞成 error dict ——
    即异步执行路径上 a11y 门禁从未真正产出过结果（静默失效）。
    本函数在异步栈上 await 取 HTML 后复用同一套规则，两条路径共用同一事实源。
    """
    a11y_cfg = {**DEFAULT_A11Y_CONFIG, **(target_cfg.get("a11y") or {})}
    if not a11y_cfg.get("enabled", True):
        return {
            "enabled": False,
            "source": a11y_cfg.get("source", "step4"),
            "total": 0, "by_severity": {}, "findings": [], "error": "",
        }
    source = a11y_cfg.get("source", "step4")
    max_findings = int(a11y_cfg.get("max_findings", 100))
    if page is None:
        return {
            "enabled": True, "source": source, "total": 0,
            "by_severity": {}, "findings": [], "error": "page 为空，无法采集 HTML",
        }
    try:
        html = await page.content()
    except Exception as exc:
        return {
            "enabled": True, "source": source, "total": 0,
            "by_severity": {}, "findings": [],
            "error": f"page.content() 失败: {str(exc)[:160]}",
        }
    return _run_rules_on_html(html, source, max_findings)