# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""可访问性（a11y）全量门禁 — 汇总层可选维度（fail-open）。

与 GAP-04/05/08、F1-F10 等 _flag_* 汇总层增强同构：
- 声明驱动：用例/配置声明 ``checks.a11y = {"enabled": true}``（或页面级默认开启
  ``A11Y_GATE_ENABLED=true`` / ``config.a11y_gate_enabled=True``）时，对目标页面
  跑 ``src/extensions/a11y`` 的 8 规则检查器（img-alt / label 关联 / button 文本 /
  heading 跳级 / a-href / aria-label / 重复 id，规则驱动无 AI 依赖）；
- fail-open：未声明不跑；有违规默认只标 ``[a11y_violations:N]`` 不改 passed；
  opt-in ``fail_on_a11y=True`` 才硬失败；
- 无页面 / 取 HTML 失败 → 记 skipped，不标、不改 passed（绝不误杀）。

[已复核-DeepSeek-V3 2026-08-16]
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)

_A11Y_TAG_PREFIX = "[a11y_violations:"


def _a11y_declared(_decl: Any) -> bool:
    """声明判定：checks.a11y.enabled，或页面级默认开启（env/config 配置声明通道）。

    两通道满足其一即视为声明：
      1. 用例级：checks["a11y"]["enabled"] truthy
      2. 页面级默认：环境变量 A11Y_GATE_ENABLED=true，或 config.a11y_gate_enabled=True
         （供 browser 主路径在不改 step_checks 白名单的情况下启用全量门禁）
    """
    if isinstance(_decl, dict) and _decl.get("enabled"):
        return True
    with guard("_a11y_declared: import os", log=_log):
        import os

        _env = str(os.environ.get("A11Y_GATE_ENABLED", "") or "").strip().lower()
        if _env in ("1", "true", "yes", "on"):
            return True
    with guard("_a11y_declared: from src.core.config_loader import c", log=_log):
        from src.core.config_loader import config

        if getattr(config, "a11y_gate_enabled", False):
            return True
    return False


async def _fetch_page_html(page) -> str:
    """安全获取页面 HTML；失败返回空串（由调用方 fail-open 跳过，绝不误杀）。"""
    if page is None:
        return ""
    try:
        return await page.content() or ""
    except Exception as e:
        _log.debug("[a11y] 获取页面 HTML 失败(跳过): %s", str(e)[:80])
        return ""


async def _flag_a11y(result: dict, page, checks: dict | None = None) -> dict:
    """★ a11y 全量门禁：页面可访问性维度标注（fail-open）。

    Args:
        result: verify_step 汇总后的结果（含 ui/api/db 三轨）。
        page: 当前 Playwright Page（可空；用于取页面 HTML）。
        checks: 本步 checks；声明位为 ``checks["a11y"] = {"enabled": true}``。
            省略时回落读取 result["a11y"]（与既有 _flag_* 单参调用同构）。
            页面级默认开启：env ``A11Y_GATE_ENABLED=true`` 或
            ``config.a11y_gate_enabled=True``。

    Returns:
        result（原地增强）：
          - 命中违规：result["a11y"]=检查报告、result["a11y_violations"]=N，
            ui 轨 evidence 与 summary 追加 ``[a11y_violations:N]``；
            仅当声明 fail_on_a11y=True 才额外置 result["passed"]=False。
          - 无违规：result["a11y"]["passed"]=True，不标 tag。
          - 未声明 / 无页面：不标、不改 passed（fail-open）。
    """
    if not isinstance(result, dict):
        return result
    _decl = (checks or {}).get("a11y") or result.get("a11y") or {}
    result["a11y"] = result.get("a11y") or {}
    if not _a11y_declared(_decl):
        # 未声明 → 零影响（仅记录未启用，供报告层区分「未跑」与「跑过无违规」）
        result["a11y"]["enabled"] = False
        return result

    result["a11y"]["enabled"] = True
    html = await _fetch_page_html(page)
    if not html:
        result["a11y"]["skipped"] = "page_unavailable"
        result["a11y"]["total"] = 0
        result["a11y"]["passed"] = True
        result["a11y_violations"] = 0
        return result

    from src.extensions.a11y import run_a11y_check

    _source = ""
    try:
        _source = str(page.url) if page else ""
    except Exception:
        _source = ""
    _report = run_a11y_check(html, source=_source)
    result["a11y"].update(_report)
    _n = int(_report.get("total") or 0)
    result["a11y_violations"] = _n
    if _n <= 0:
        result["a11y"]["passed"] = True
        return result

    result["a11y"]["passed"] = False
    _tag = "%s%d]" % (_A11Y_TAG_PREFIX, _n)
    _by_sev = _report.get("by_severity") or {}
    _sev = "; ".join("%s=%d" % (k, _by_sev[k]) for k in sorted(_by_sev)) or "none"
    _sample = "; ".join(
        "%s(%s):%s"
        % (f.get("rule", "?"), f.get("severity", "?"), f.get("snippet", ""))
        for f in (_report.get("findings") or [])[:5]
    )
    _detail = "a11y 检查发现 %d 条违规(%s) 样本: %s" % (_n, _sev, _sample)

    # 写入 ui 轨 evidence（与既有 _flag_* 同构：轨 evidence + summary 追加）
    _ui = result.get("ui")
    if isinstance(_ui, dict):
        _ui_evidence = str(_ui.get("evidence", "") or "")
        if _tag not in _ui_evidence:
            _ui["evidence"] = (_ui_evidence + " " + _tag + " " + _detail).strip()[:500]
            result["ui"] = _ui
    _summary = str(result.get("summary", "") or "")
    if _tag not in _summary:
        result["summary"] = (_summary + " " + _tag).strip()[:500]

    if isinstance(_decl, dict) and _decl.get("fail_on_a11y"):
        # opt-in 升级为硬失败（默认关闭；存量用例未声明 a11y → 永不触发）
        result["passed"] = False

    _log.warning(
        "[a11y] 页面存在 %d 条可访问性违规 → 标注 %s | %s",
        _n,
        _tag,
        _detail[:200],
    )
    return result
