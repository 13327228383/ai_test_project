# -*- coding: utf-8 -*-
"""性能指标采集 — Web（Playwright）+ API（ApiTracker）→ :class:`PerfSample`。

采集口径：
  - **Web**：W3C Navigation Timing（ttfb/dcl/load）+ Resource Timing
    （count/bytes）+ ``window.__perfMetrics``（longtask/cls，由注入的
    ``PerformanceObserver`` 累加）。
  - **API**：复用 step4 ``ApiTracker`` 的 ``page.on('response')`` 记录，
    归一为 ``elapsed_ms`` / ``status_code`` / ``payload_bytes``。

fail-closed：采集失败**返回带 ``error`` 的 PerfSample 而非 None**，
交由 gate 判为 ``NO_DATA``，绝不静默丢弃。

本模块不认任何靶子名，只认 kind / method / url，靶子差异全部由
``perf_rules.yaml`` 承担。

⚠️ 本仓库 step4 使用 **async Playwright**（``playwright.async_api``），
``page.evaluate`` / ``page.add_init_script`` 均返回协程，必须 await。
因此对外提供**同步 + 异步双份入口**：

  - :meth:`MetricCollector.install` / :meth:`collect_web`
    —— 同步 Page（单测 fake page、sync_api）专用。
  - :meth:`MetricCollector.install_async` / :meth:`collect_web_async`
    —— async Page 专用，插件走这条路径。

两条路径共用同一套 key 归一化与指标清洗逻辑（``_finish_web``），
不存在重复实现。

[已复核-DeepSeek-V3 2026-08-16]
"""

from __future__ import annotations
from src.core.guarded import guard

import inspect
import logging
import threading
from typing import Any

from src.extensions.perf_baseline.schemas import (
    KIND_API,
    KIND_WEB,
    PerfSample,
    normalize_key,
    now_ts,
)

_log = logging.getLogger(__name__)

# ── 已注入 init_script 的 page（按 id 去重，保证 install 幂等）──
_installed_page_ids: set[int] = set()
_install_lock = threading.RLock()
_MAX_TRACKED_PAGES = 512

#: 非负且允许为 0 的指标（其余指标 <=0 视为无效并剔除）
_ZERO_ALLOWED = frozenset(
    {"resource_count", "resource_bytes", "longtask_ms", "cls_approx", "payload_bytes"}
)


class MetricCollector:
    """无状态采集器（全部方法为 ``staticmethod``，可直接类调用）。"""

    #: 注入脚本：在每个新文档最早时机安装 PerformanceObserver
    INIT_SCRIPT: str = """
(() => {
  if (window.__perfMetricsInstalled) { return; }
  window.__perfMetricsInstalled = true;
  window.__perfMetrics = { cls: 0, longtask_ms: 0, longtask_count: 0 };
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (!entry.hadRecentInput) {
          window.__perfMetrics.cls += (entry.value || 0);
        }
      }
    }).observe({ type: 'layout-shift', buffered: true });
  } catch (e) { /* 浏览器不支持 layout-shift，静默降级 */ }
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        window.__perfMetrics.longtask_ms += (entry.duration || 0);
        window.__perfMetrics.longtask_count += 1;
      }
    }).observe({ type: 'longtask', buffered: true });
  } catch (e) { /* 浏览器不支持 longtask，静默降级 */ }
})();
"""

    #: 读取指标的 JS（一次 evaluate 拿全，减少往返）
    COLLECT_SCRIPT: str = """
() => {
  const out = {};
  try {
    const nav = performance.getEntriesByType('navigation')[0];
    if (nav) {
      out.ttfb_ms = Math.max(0, nav.responseStart - nav.startTime);
      out.dcl_ms = Math.max(0, nav.domContentLoadedEventEnd - nav.startTime);
      const loadEnd = nav.loadEventEnd > 0 ? nav.loadEventEnd : nav.domComplete;
      out.load_ms = Math.max(0, loadEnd - nav.startTime);
    }
  } catch (e) { /* Navigation Timing 不可用 */ }
  try {
    const res = performance.getEntriesByType('resource') || [];
    out.resource_count = res.length;
    let bytes = 0;
    for (const r of res) { bytes += (r.transferSize || 0); }
    out.resource_bytes = bytes;
  } catch (e) { /* Resource Timing 不可用 */ }
  try {
    const pm = window.__perfMetrics || {};
    out.longtask_ms = pm.longtask_ms || 0;
    out.cls_approx = pm.cls || 0;
  } catch (e) { /* 注入脚本未生效 */ }
  return out;
}
"""

    # ── 注入 ───────────────────────────────────────────────────────────
    @staticmethod
    def _claim_install(page: Any) -> bool:
        """幂等登记：返回 ``True`` 表示本次需要真正注入。"""
        page_id = id(page)
        with _install_lock:
            if page_id in _installed_page_ids:
                return False
            if len(_installed_page_ids) > _MAX_TRACKED_PAGES:
                _installed_page_ids.clear()
            _installed_page_ids.add(page_id)
        return True

    @staticmethod
    def _release_install(page: Any) -> None:
        """注入失败时撤销登记，允许下次重试。"""
        with _install_lock:
            _installed_page_ids.discard(id(page))

    @staticmethod
    def install(page: Any) -> None:
        """向**同步** page 注入 ``INIT_SCRIPT``（幂等，同一 page 只注入一次）。

        若传入的是 async Page（``add_init_script`` 返回协程），本方法会关闭
        协程并 warning —— 请改用 :meth:`install_async`。注入失败只 warning，
        不影响主链路。
        """
        if page is None:
            return
        if not MetricCollector._claim_install(page):
            return
        try:
            result = page.add_init_script(MetricCollector.INIT_SCRIPT)
            if inspect.isawaitable(result):
                _close_awaitable(result)
                MetricCollector._release_install(page)
                _log.warning("[perf_gate] 同步 install 收到 async Page，请改用 install_async")
                return
            _log.debug("[perf_gate] init_script 已注入 page=%s", id(page))
        except Exception as exc:  # noqa: BLE001
            MetricCollector._release_install(page)
            _log.warning("[perf_gate] init_script 注入失败(不阻断): %s", str(exc)[:120])

    @staticmethod
    async def install_async(page: Any) -> None:
        """向 **async** page 注入 ``INIT_SCRIPT``（幂等）。兼容同步 Page。"""
        if page is None:
            return
        if not MetricCollector._claim_install(page):
            return
        try:
            result = page.add_init_script(MetricCollector.INIT_SCRIPT)
            if inspect.isawaitable(result):
                await result
            _log.debug("[perf_gate] init_script 已注入(async) page=%s", id(page))
        except Exception as exc:  # noqa: BLE001
            MetricCollector._release_install(page)
            _log.warning("[perf_gate] init_script 注入失败(不阻断): %s", str(exc)[:120])

    @staticmethod
    def reset_install_state() -> None:
        """清空注入记录（供单测使用）。"""
        with _install_lock:
            _installed_page_ids.clear()

    # ── Web 采集 ───────────────────────────────────────────────────────
    @staticmethod
    def _web_url(page: Any) -> str:
        """安全读取 page.url。失败返回空串。"""
        try:
            return str(getattr(page, "url", "") or "")
        except Exception:  # noqa: BLE001 — 取 url 失败不影响后续
            return ""

    @staticmethod
    def _finish_web(
        raw: Any,
        url: str,
        target: str,
        run_id: str,
        case_id: str,
        error: str = "",
    ) -> PerfSample:
        """同步/异步两条路径共用的样本构造出口（唯一实现，不重复造轮子）。"""
        key = normalize_key(KIND_WEB, "", url)
        if error:
            return PerfSample(
                kind=KIND_WEB, key=key, target=target, run_id=run_id,
                case_id=case_id, raw_url=url, error=error,
            )
        metrics = _sanitize_metrics(raw)
        if not metrics:
            return PerfSample(
                kind=KIND_WEB, key=key, target=target, run_id=run_id,
                case_id=case_id, raw_url=url,
                error="Web 指标为空（页面可能未完成导航）",
            )
        return PerfSample(
            kind=KIND_WEB,
            key=key,
            target=target,
            run_id=run_id,
            case_id=case_id,
            raw_url=url,
            metrics=metrics,
            collected_at=now_ts(),
        )

    @staticmethod
    def collect_web(
        page: Any,
        target: str,
        run_id: str,
        case_id: str = "",
    ) -> PerfSample:
        """采集**同步** Page 的 Web 性能指标。

        参数：
            page: Playwright 同步 Page 对象（或单测 fake page）。
            target / run_id / case_id: 归属信息。

        返回：
            :class:`PerfSample`。采集失败时 ``error`` 非空、``metrics`` 为空
            （fail-closed，由 gate 判为 ``NO_DATA``）。
        """
        url = MetricCollector._web_url(page)
        if page is None:
            return MetricCollector._finish_web(
                None, url, target, run_id, case_id, error="page 为空，无法采集"
            )
        try:
            raw = page.evaluate(MetricCollector.COLLECT_SCRIPT)
        except Exception as exc:  # noqa: BLE001
            message = f"page.evaluate 失败: {str(exc)[:120]}"
            _log.warning("[perf_gate] Web 采集失败: %s", message)
            return MetricCollector._finish_web(
                None, url, target, run_id, case_id, error=message
            )
        if inspect.isawaitable(raw):
            _close_awaitable(raw)
            return MetricCollector._finish_web(
                None, url, target, run_id, case_id,
                error="同步 collect_web 收到 async Page，请改用 collect_web_async",
            )
        return MetricCollector._finish_web(raw, url, target, run_id, case_id)

    @staticmethod
    async def collect_web_async(
        page: Any,
        target: str,
        run_id: str,
        case_id: str = "",
    ) -> PerfSample:
        """采集 **async** Page 的 Web 性能指标（兼容同步 Page）。

        语义与 :meth:`collect_web` 完全一致，只是会 await ``page.evaluate``。
        """
        url = MetricCollector._web_url(page)
        if page is None:
            return MetricCollector._finish_web(
                None, url, target, run_id, case_id, error="page 为空，无法采集"
            )
        try:
            raw = page.evaluate(MetricCollector.COLLECT_SCRIPT)
            if inspect.isawaitable(raw):
                raw = await raw
        except Exception as exc:  # noqa: BLE001
            message = f"page.evaluate 失败: {str(exc)[:120]}"
            _log.warning("[perf_gate] Web 采集失败: %s", message)
            return MetricCollector._finish_web(
                None, url, target, run_id, case_id, error=message
            )
        return MetricCollector._finish_web(raw, url, target, run_id, case_id)

    # ── API 采集 ───────────────────────────────────────────────────────
    @staticmethod
    def collect_api(
        calls: list[dict],
        target: str,
        run_id: str,
        case_id: str = "",
    ) -> list[PerfSample]:
        """把 ``ApiTracker`` 的调用记录归一为 API 样本。

        同一归一化 key 的多次调用会被合并，**每个指标取本轮最大值**
        （最坏情况代表该业务点的性能上界，符合门禁语义）。

        参数：
            calls: ``ApiTracker._recent_requests`` 风格的 dict 列表。

        返回：
            :class:`PerfSample` 列表；无有效调用时返回空列表（**不产生
            NO_DATA 噪声** —— 「本轮没发生 API 调用」不是「采集失败」）。
        """
        if not isinstance(calls, (list, tuple)) or not calls:
            return []

        aggregated: dict[str, dict[str, Any]] = {}
        for call in calls:
            if not isinstance(call, dict):
                continue
            url = str(call.get("url") or "")
            if not url:
                continue
            if not call.get("has_response"):
                continue  # 无响应的请求无耗时可言，跳过（非采集失败）
            method = str(call.get("method") or "GET")
            key = normalize_key(KIND_API, method, url)

            metrics = _sanitize_metrics(
                {
                    "elapsed_ms": call.get("elapsed_ms"),
                    "status_code": call.get("status"),
                    "payload_bytes": call.get("resp_bytes"),
                }
            )
            if not metrics:
                continue

            slot = aggregated.setdefault(key, {"raw_url": url, "metrics": {}})
            for name, value in metrics.items():
                previous = slot["metrics"].get(name)
                slot["metrics"][name] = value if previous is None else max(previous, value)

        samples: list[PerfSample] = []
        stamp = now_ts()
        for key, slot in aggregated.items():
            samples.append(
                PerfSample(
                    kind=KIND_API,
                    key=key,
                    target=target,
                    run_id=run_id,
                    case_id=case_id,
                    raw_url=slot["raw_url"],
                    metrics=slot["metrics"],
                    collected_at=stamp,
                )
            )
        return samples

    # ── 便捷别名（与 schemas 单点实现保持一致，不重复造轮子）──────────
    normalize_key = staticmethod(normalize_key)


def _close_awaitable(obj: Any) -> None:
    """关闭未被 await 的协程，避免 "coroutine was never awaited" 警告。"""
    with guard("_close_awaitable: close = getattr(obj, 'close', None)", log=_log):
        close = getattr(obj, "close", None)
        if callable(close):
            close()


def _sanitize_metrics(raw: Any) -> dict[str, float]:
    """清洗原始指标：剔除 None / 非数值 / 非法（<=0 且不允许为 0）的项。

    ``resource_count`` / ``cls_approx`` 等天然可为 0 的指标保留 0 值。
    """
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, float] = {}
    for name, value in raw.items():
        if value is None:
            continue
        try:
            num = float(value)
        except (TypeError, ValueError):
            continue
        if num != num or num in (float("inf"), float("-inf")):  # NaN / inf
            continue
        if num < 0:
            continue
        if num == 0 and str(name) not in _ZERO_ALLOWED:
            continue
        cleaned[str(name)] = round(num, 2)
    return cleaned


__all__ = ["MetricCollector"]
