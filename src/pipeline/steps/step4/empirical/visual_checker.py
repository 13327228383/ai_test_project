# -*- coding: utf-8 -*-
"""视觉横切门禁 — step4 集成包装器。

将 ``_verify_visual`` 的三态终态（PASS/FAIL/NO_DATA）接入 step4 常规流水线，
作为独立验证维度写入报告（对称 a11y / perf 横切维度）。

设计约束：
  - 配置驱动、不写死靶子：是否启用与期望描述全部来自 ``target_cfg["visual"]``；
  - 诚实 fail-closed：视觉无法无期望硬判。未配置 ``expectation`` / 截图失败 /
    VLM（含本地降级）不可用 → 一律返回 ``NO_DATA``（passed=None，绝不伪造 PASS）；
  - 判定核心复用 ``_verify_visual._try_vlm_fallback``（云端 → VLMOracle → Local
    Backend → 降级链），不重复实现视觉算法；
  - 浏览器执行器为同步 Playwright，而 VLM 判定是 asyncio：此处同步截图后
    ``asyncio.run`` 包装调用（判定不需要页面 DOM 文本，视觉只看图 → 传 page=None）。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any

from src.pipeline.steps.step4.verification import _verify_visual as _vv

_log = logging.getLogger(__name__)


def _run_vlm_off_loop(
    screenshot_bytes: bytes, expectation: str, timeout: float = 180.0
) -> Any:
    """在独立线程的新事件循环中执行视觉统一降级链。

    ★ 根因修复（2026-08-29）: ``run_visual_gate`` 在 ``with sync_playwright()``
    快内被调用，此时当前线程已在 playwright 驱动的事件循环中，直接
    ``asyncio.run`` 会抛 "Cannot be called from a running event loop" 且协程
    从未被 await（wand, fail 但恒 NO_DATA）。正确做法是把协程丢到**新线程**再
    ``asyncio.run``——新线程无 running loop，可安全执行 VLM 判定（VLMOracle 内
    部对 Ollama 已是同步调用，放在后台线程运行亦安全）。

    ★ 链路归一到 ``_vv.verify_visual_bytes``（云端 → 本地 → LocalBackend → NO_DATA）：
    本门禁此前只调用本地降级段，导致配置了云端视觉模型时实证门禁也永不走云端。
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
        _fut = _ex.submit(
            lambda: asyncio.run(
                _vv.verify_visual_bytes(screenshot_bytes, expectation, None)
            )
        )
        return _fut.result(timeout=timeout)

#: 靶子级视觉门禁配置默认值
DEFAULT_VISUAL_CONFIG: dict[str, Any] = {
    "enabled": True,     # 默认开启（可在 target_cfg["visual"] 关闭）
    "source": "step4",   # 来源标识（写入报告）
    "expectation": "",   # 期望描述（配置驱动；空则诚实 NO_DATA，不硬判）
}


def run_visual_gate(page: Any, target_cfg: dict) -> dict[str, Any]:
    """在 browser 模式用例执行后，对当前页面运行视觉横切门禁。

    参数：
        page: Playwright **同步** Page 对象（已导航到目标页面）。
        target_cfg: 靶子配置 dict（含可选的 ``visual`` 段）。

    返回：
        dict: {enabled, source, status, passed, skipped, confidence, reason,
               evidence, error}。``enabled=False`` 时返回空结果；采集失败时
        ``error`` 非空；未做实际校验时 ``status="NO_DATA"`` 且 ``passed=None``。
    """
    v_cfg = {**DEFAULT_VISUAL_CONFIG, **(target_cfg.get("visual") or {})}
    source = v_cfg.get("source", "step4")

    if not v_cfg.get("enabled", True):
        return {
            "enabled": False,
            "source": source,
            "status": "",
            "passed": None,
            "skipped": True,
            "confidence": 0.0,
            "reason": "",
            "evidence": "",
            "error": "",
        }

    expectation = (v_cfg.get("expectation") or "").strip()
    if not expectation:
        return {
            "enabled": True,
            "source": source,
            "status": _vv.STATUS_NO_DATA,
            "passed": None,
            "skipped": True,
            "confidence": 0.0,
            "reason": _vv.REASON_NO_EXPECTATION,
            "evidence": "未配置 visual.expectation，视觉门禁诚实跳过（不硬判）",
            "error": "",
        }

    if page is None:
        return {
            "enabled": True,
            "source": source,
            "status": _vv.STATUS_NO_DATA,
            "passed": None,
            "skipped": True,
            "confidence": 0.0,
            "reason": _vv.REASON_NO_PAGE,
            "evidence": "page 为空，无法截图，视觉门禁未校验",
            "error": "",
        }

    # 同步截图（纯内存，不落盘）
    try:
        screenshot_bytes = page.screenshot(full_page=False)
    except Exception as exc:
        _log.warning("[visual_gate] 截图失败: %s", str(exc)[:80])
        return {
            "enabled": True,
            "source": source,
            "status": _vv.STATUS_NO_DATA,
            "passed": None,
            "skipped": True,
            "confidence": 0.0,
            "reason": _vv.REASON_SCREENSHOT_FAILED,
            "evidence": "截图失败(%s)，视觉门禁未校验" % str(exc)[:60],
            "error": str(exc)[:120],
        }
    if not screenshot_bytes:
        return {
            "enabled": True,
            "source": source,
            "status": _vv.STATUS_NO_DATA,
            "passed": None,
            "skipped": True,
            "confidence": 0.0,
            "reason": _vv.REASON_SCREENSHOT_FAILED,
            "evidence": "截图为空，视觉门禁未校验",
            "error": "",
        }

    # 判定核心复用降级链（page=None：视觉只看图，不依赖 DOM 文本）
    try:
        result = _run_vlm_off_loop(screenshot_bytes, expectation)
    except Exception as re_:  # 超时/线程异常等 → 诚实降级 NO_DATA
        _log.warning("[visual_gate] VLM 判定失败: %s", str(re_)[:100])
        result = None

    if result is None:
        result = _vv._no_data(
            _vv.REASON_NO_VISION_PROVIDER, "视觉回退链全部不可用，门禁未校验"
        )

    return {
        "enabled": True,
        "source": source,
        "status": result.get("status", _vv.STATUS_NO_DATA),
        "passed": result.get("passed"),
        "skipped": bool(result.get("skipped", False)),
        "confidence": float(result.get("confidence", 0.0) or 0.0),
        "reason": str(result.get("reason", "") or ""),
        "evidence": str(result.get("evidence", "") or ""),
        "error": "",
    }