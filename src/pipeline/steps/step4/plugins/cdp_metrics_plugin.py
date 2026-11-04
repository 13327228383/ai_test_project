# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
CDP 指标收集插件
================
在 CDP（客户端浏览器）模式下，采集单条用例的执行指标：
- 用例通过/失败
- 执行耗时
- action 数量
- 错误信息

介入点：AFTER_INTENT（用例执行后）
存储：ai_health_metrics 表（metric_type='cdp_browser'）

设计原则（AGENTS.md §6）：
- 继承 BasePlugin，独立 try-except，单个失败不影响其他
- 只观察和增强，不修改核心数据流输入输出格式
- 非侵入式：通过 hook 获取上下文，不修改主链路代码
"""

import logging
from typing import Any

from .plugin_manager import BasePlugin

_log = logging.getLogger(__name__)


class CdpMetricsPlugin(BasePlugin):
    """CDP 客户端浏览器指标收集插件。

    仅在 CDP 模式下生效（engine._browser_info.cdp_endpoint 存在时）。
    服务端浏览器模式下不采集（避免与服务端指标重复）。
    """

    name = "cdp_metrics"
    enabled = True
    config: dict[str, Any] = {}

    def __init__(self, config: dict[str, Any] = None):
        super().__init__(config)
        self.enabled = self.config.get("enabled", True)

    def on_after_intent(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """用例执行后采集 CDP 指标。

        ctx 包含: case_id, case_meta, passed, step_results, engine, ai, intent_elapsed
        """
        if not self.enabled:
            return None

        with guard("on_after_intent: engine = ctx.get('engine')", log=_log):
            engine = ctx.get("engine")
            if engine is None:
                return None

            # 判断是否 CDP 模式
            browser_info = getattr(engine, "_browser_info", {}) or {}
            cdp_endpoint = browser_info.get("cdp_endpoint")
            if not cdp_endpoint:
                return None  # 服务端模式，不采集

            case_id = ctx.get("case_id", "")
            passed = bool(ctx.get("passed", False))
            step_results = ctx.get("step_results", []) or []
            intent_elapsed = ctx.get("intent_elapsed", 0.0) or 0.0

            # 汇总指标
            duration_ms = round(float(intent_elapsed) * 1000, 1)
            action_count = len(step_results)
            # 截图大小：从 step_results 汇总（如有 screenshot_size 字段）
            screenshot_size = 0
            for r in step_results:
                if isinstance(r, dict):
                    ss = r.get("screenshot_size", 0)
                    if isinstance(ss, (int, float)) and ss > 0:
                        screenshot_size += int(ss)

            # 获取 project / run_id / version_id
            from src.extensions.metrics_collector import metrics_collector
            from src.core.config_loader import config
            from src.backend.state import (
                _current_project,
                _current_version,
                _active_runs,
            )

            _proj = _current_project() or getattr(config, "_project_name", "")
            _ver = _current_version() or ""
            _run_id = ""
            try:
                with __import__(
                    "src.backend.state", fromlist=["_active_runs_lock"]
                )._active_runs_lock:
                    if _active_runs:
                        _run_id = next(iter(_active_runs.keys()), "")
            except Exception as _e:
                _log.debug(
                    "[cdp_metrics_plugin] _active_runs 读取异常: %s", _e, exc_info=True
                )

            if not _proj or not _run_id:
                _log.debug("[CdpMetrics] 缺少 project/run_id，跳过埋点")
                return None

            # 记录用例执行指标
            metrics_collector.record_cdp_browser_execution(
                project=_proj,
                run_id=_run_id,
                case_id=case_id,
                success=passed,
                duration_ms=duration_ms,
                action_count=action_count,
                screenshot_size=screenshot_size,
                version_id=_ver,
            )

            # 失败时记录错误
            if not passed:
                error_msg = ""
                error_type = "case_failed"
                for r in step_results:
                    if isinstance(r, dict) and not r.get("passed", True):
                        err = r.get("error", "")
                        if err:
                            error_msg = (error_msg + "; " + err) if error_msg else err
                            break
                metrics_collector.record_cdp_browser_error(
                    project=_proj,
                    run_id=_run_id,
                    case_id=case_id,
                    error_type=error_type,
                    error_message=error_msg[:500] if error_msg else "用例执行失败",
                    version_id=_ver,
                )

                # ★ Phase 4: 通过 SSE 推送结构化告警（复用现有 _broadcast_log）
                self._push_alert_to_sse(
                    case_id=case_id,
                    error_type=error_type,
                    error_message=error_msg[:200] if error_msg else "用例执行失败",
                    duration_ms=duration_ms,
                )

            _log.debug(
                "[CdpMetrics] case=%s passed=%s dur=%.0fms actions=%d",
                case_id,
                passed,
                duration_ms,
                action_count,
            )

        return None

    def _push_alert_to_sse(
        self, case_id: str, error_type: str, error_message: str, duration_ms: float
    ) -> None:
        """通过 SSE 日志流推送结构化告警 JSON（复用现有 _broadcast_log）。

        格式：{"_alert": true, "alert_type": "cdp_case_failed", ...}
        前端在 SSE onmessage 中识别 _alert 字段并展示 Toast + 跳转链接。
        """
        with guard("_push_alert_to_sse: import json", log=_log):
            import json
            from src.backend.log_capture import _broadcast_log

            alert_payload = {
                "_alert": True,
                "alert_type": "cdp_case_failed",
                "case_id": case_id,
                "error_type": error_type,
                "message": error_message,
                "duration_ms": duration_ms,
                "jump_url": f"/intent-edit?case_id={case_id}",
            }
            _broadcast_log(
                json.dumps(alert_payload, ensure_ascii=False),
                level="WARNING",
                step="4",
            )
