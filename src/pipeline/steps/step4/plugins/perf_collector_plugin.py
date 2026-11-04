# -*- coding: utf-8 -*-
"""
性能门禁采集插件（perf_collector）
==================================
职责：在 step4 执行链路上采集性能样本并投递到 ``PerfContext``，供 step5
``_perf_gate_report`` 做基线对比与门禁判定。

与既有 ``performance_monitor`` 插件的分工（**两者并列、互不干扰**）：
  - ``performance_monitor``：实时阈值告警（硬编码阈值 → ai_health_metrics）
  - ``perf_collector``（本插件）：基线门禁数据源（规则驱动 → PerfContext）

介入点（★ 均为 ``_base.py`` 通过 ``execute_hook`` **实际触发且 await** 的钩子）::

    on_before_intent  → install_async(page)     注入 PerformanceObserver
    on_after_step     → collect_web_async(page) 页面 key 变化时采一次
    on_after_intent   → collect_web_async + collect_api(ApiTracker 增量)

⚠️ **偏离设计文档的说明**：设计文档写的是 ``ON_NAVIGATE`` 钩子，但经全仓库
核查 ``PluginHook.ON_NAVIGATE`` **从未被任何代码 emit**（仅 enum 与 BasePlugin
空实现存在，``performance_monitor.on_navigate`` 同样是死代码）。若照抄设计
文档，本插件将永远不会被调用。故改挂 ``before_intent / after_step /
after_intent`` 三个真实钩子，语义等价（after_step 用 URL key 变化检测替代
"导航事件"）。``on_navigate`` 仍保留实现，一旦将来有人 emit 该钩子即可生效。

⚠️ 本仓库使用 **async Playwright**，且 ``PluginManager.emit``（同步）会直接
``close()`` 掉协程。因此所有 hook 必须是 ``async def`` 并只经由
``execute_hook`` 调用 —— 这也是采集器要用 ``*_async`` 变体的原因。

工程约束：
  - **零副作用**：``enabled=false`` 时所有 hook 立即返回 ``None``。
  - **绝不抛异常**：全部 hook 整体 try-except，异常只 warning。
  - **返回 None**：不向 ctx 注入任何键，避免触发 ``plugin_interventions`` 写库。

[已复核-DeepSeek-V3 2026-08-16]
"""

from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any

from src.pipeline.steps.step4.plugins.plugin_manager import BasePlugin

_log = logging.getLogger(__name__)


class PerfCollectorPlugin(BasePlugin):
    """性能门禁采集插件。"""

    name = "perf_collector"

    def __init__(self, config: dict[str, Any] = None):
        super().__init__(config)
        # ApiTracker 增量游标（避免同一批调用被重复采集）
        self._api_cursor_ts: float = 0.0
        self._sample_count: int = 0
        # 已采集过的 (run_id, web_key)，同一页面每轮只采一次，避免报告噪声
        self._seen_web: set[tuple[str, str]] = set()
        if self.enabled:
            with guard("__init__: from src.extensions.perf_baseline.co", log=_log, level=logging.WARNING):
                from src.extensions.perf_baseline.context import perf_context

                perf_context.configure(spill_enabled=bool(self.config.get("spill", True)))

    # ── 钩子：用例开始前（注入观察器）───────────────────────────────────
    async def on_before_intent(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """注入 ``PerformanceObserver`` init_script（幂等）。

        必须尽早注入：``layout-shift`` / ``longtask`` 只能观测注入之后发生的
        事件（``buffered: true`` 可补齐同文档内的历史条目）。
        """
        if not self.enabled:
            return None
        with guard("on_before_intent: page = self._resolve_page(ctx)", log=_log, level=logging.WARNING):
            page = self._resolve_page(ctx)
            if page is None:
                return None
            from src.extensions.perf_baseline.collector import MetricCollector

            await MetricCollector.install_async(page)
        return None

    # ── 钩子：单步执行后（页面切换时采 Web 指标）─────────────────────────
    async def on_after_step(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """页面 URL key 发生变化时采集一次 Web 指标（等价于"导航后采集"）。"""
        if not self.enabled:
            return None
        await self._collect_web_once(ctx)
        return None

    # ── 钩子：用例执行后（补采 Web + 采 API）────────────────────────────
    async def on_after_intent(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """用例结束后补采终态页面 Web 指标，并采集本用例期间的 API 指标。"""
        if not self.enabled:
            return None
        await self._collect_web_once(ctx)
        with guard("on_after_intent: calls = self._resolve_api_calls(ctx)", log=_log, level=logging.WARNING):
            calls = self._resolve_api_calls(ctx)
            if not calls:
                return None

            from src.extensions.perf_baseline.collector import MetricCollector
            from src.extensions.perf_baseline.context import perf_context

            run_id = self._resolve_run_id(ctx)
            samples = MetricCollector.collect_api(
                calls,
                target=self._resolve_target(ctx),
                run_id=run_id,
                case_id=str(ctx.get("case_id") or ""),
            )
            perf_context.put_many(run_id, samples)
            self._sample_count += len(samples)
        return None

    # ── 钩子：导航（当前仓库未 emit，保留以备将来接线）───────────────────
    async def on_navigate(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """页面导航后采集 Web 指标。**当前仓库不触发此钩子**（见模块 docstring）。"""
        if not self.enabled:
            return None
        await self._collect_web_once(ctx)
        return None

    # ── 采集主体 ───────────────────────────────────────────────────────
    async def _collect_web_once(self, ctx: dict[str, Any]) -> None:
        """对当前页面采集一次 Web 指标；同一 (run_id, key) 只采一次。"""
        with guard("_collect_web_once: page = self._resolve_page(ctx)", log=_log, level=logging.WARNING):
            page = self._resolve_page(ctx)
            if page is None:
                return

            from src.extensions.perf_baseline.collector import MetricCollector
            from src.extensions.perf_baseline.context import perf_context
            from src.extensions.perf_baseline.schemas import KIND_WEB, normalize_key

            url = MetricCollector._web_url(page)
            if not url:
                return
            run_id = self._resolve_run_id(ctx)
            dedup_key = (run_id, normalize_key(KIND_WEB, "", url))
            if dedup_key in self._seen_web:
                return

            # 兜底注入（before_intent 可能因 page 尚未创建而错过）
            await MetricCollector.install_async(page)
            sample = await MetricCollector.collect_web_async(
                page,
                target=self._resolve_target(ctx),
                run_id=run_id,
                case_id=str(ctx.get("case_id") or ""),
            )
            self._seen_web.add(dedup_key)
            perf_context.put(run_id, sample)
            self._sample_count += 1

    # ── 解析辅助 ───────────────────────────────────────────────────────
    @staticmethod
    def _resolve_page(ctx: dict[str, Any]) -> Any:
        """从 ctx 解析 Playwright Page。取不到返回 ``None``。"""
        page = ctx.get("page")
        if page is not None:
            return page
        engine = ctx.get("engine")
        return getattr(engine, "_page", None) if engine is not None else None

    def _resolve_api_calls(self, ctx: dict[str, Any]) -> list[dict]:
        """从 ApiTracker 增量取本用例期间的调用记录，并推进游标。"""
        engine = ctx.get("engine")
        tracker = getattr(engine, "_api_tracker", None) if engine is not None else None
        if tracker is None:
            return []
        try:
            if hasattr(tracker, "get_calls_since"):
                calls = tracker.get_calls_since(self._api_cursor_ts)
            else:
                calls = list(getattr(tracker, "_recent_requests", []) or [])
        except Exception as exc:  # noqa: BLE001
            _log.warning("[perf_gate] 读取 ApiTracker 失败: %s", str(exc)[:100])
            return []

        calls = [c for c in (calls or []) if isinstance(c, dict)]
        if calls:
            try:
                self._api_cursor_ts = max(float(c.get("ts", 0) or 0) for c in calls)
            except (TypeError, ValueError) as exc:
                # 游标未推进 → 下轮会重复取到这批调用；collect_api 按 key 聚合
                # 取最大值，重复不会污染结果，仅可能多算一次，故只告警不中断。
                _log.warning("[perf_gate] ApiTracker 游标推进失败: %s", str(exc)[:100])
        return calls

    @staticmethod
    def _resolve_target(ctx: dict[str, Any]) -> str:
        """靶子名多级兜底：ctx.project → config.project_name → 当前项目 → unknown。"""
        candidate = str(ctx.get("project") or ctx.get("target") or "").strip()
        if candidate:
            return candidate
        with guard("_resolve_target: from src.core.config_loader import c", log=_log):
            from src.core.config_loader import config

            candidate = str(getattr(config, "project_name", "") or "").strip()
            if candidate:
                return candidate
        with guard("_resolve_target: from src.backend.state import _curre", log=_log):
            from src.backend.state import _current_project

            candidate = str(_current_project() or "").strip()
            if candidate:
                return candidate
        return "unknown"

    @classmethod
    def _resolve_run_id(cls, ctx: dict[str, Any]) -> str:
        """run_id 多级兜底：ctx.run_id → ``versioning.current_run_id`` → default。

        ★ 必须与 step5 ``_perf_gate_report.run_perf_gate`` 的推导方式一致，
        否则 ``perf_context.drain(run_id)`` 取不到样本。``current_run_id``
        是确定性函数（project + active version_label），两侧同 session 必然同值。
        """
        candidate = str(ctx.get("run_id") or "").strip()
        if candidate:
            return candidate
        with guard("_resolve_run_id: from src.core.versioning import curr", log=_log):
            from src.core.versioning import current_run_id

            candidate = str(current_run_id(cls._resolve_target(ctx)) or "").strip()
            if candidate:
                return candidate
        return "default"

    def get_summary(self) -> dict[str, Any]:
        """本插件本轮采集概况（诊断用）。"""
        return {
            "plugin": self.name,
            "enabled": self.enabled,
            "samples_collected": self._sample_count,
            "api_cursor_ts": self._api_cursor_ts,
            "web_keys": len(self._seen_web),
        }
