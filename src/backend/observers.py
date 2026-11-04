# -*- coding: utf-8 -*-
"""
观察者注册与日志劫持模块
========================
从 app.py 提取：_register_ai_observers, _hijack_router_logs,
log_step_ok, log_step_fail, update_monitor_dom。
"""

import time
from src.core.guarded import guard
import re
import logging

_log = logging.getLogger(__name__)
import threading

import src.backend.state as _st


_OBSERVERS_REGISTERED = False


def _register_ai_observers():
    """向 AiBrain 注册思考状态观察者回调。"""
    global _OBSERVERS_REGISTERED
    if _OBSERVERS_REGISTERED:
        return
    _OBSERVERS_REGISTERED = True
    with guard("_register_ai_observers: from src.ai.ai_client import ai_brai", log=_log, level=logging.WARNING):
        from src.ai.ai_client import ai_brain

        def on_start(name):
            _st._thinking_active = True
            _st._thinking_name = name
            _st._thinking_start_ts = time.time()
            # ★ A1: 同时写入 per-project 存储
            _st._set_thinking_active(True, name)

        def on_finish(name, elapsed, usage, cost, model="—"):
            _st._thinking_active = False
            # ★ A1: 同时清除 per-project 存储
            _st._set_thinking_active(False)
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            _st._update_token_stats(
                prompt_tokens, completion_tokens, elapsed, cost, model
            )
            # ★ 持久化到 DB：每笔 AI 调用的 token 用量写入 ai_health_metrics，
            #    供进程重启后 /api/token-stats "合计"行跨会话累加。
            #    后台线程，不阻塞 AI 调用链；无有效 project 时跳过（FK 约束）。
            # ★ v6: 附带 caller_name 到 details，供 Phase 级 Token 统计。
            _persist_token_usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                project=_st._current_project(),
                caller_name=name,
                model=model,
            )

        ai_brain.register_observer(on_start=on_start, on_finish=on_finish)

    # 预加载定价
    def _prefetch():
        with guard("_prefetch: from src.ai.pricing_fetcher import p", log=_log, level=logging.WARNING):
            from src.ai.pricing_fetcher import pricing_fetcher
            from src.core.config_loader import config as _obs_cfg

            pricing_fetcher.get_price(_obs_cfg.ai_model)

    threading.Thread(target=_prefetch, daemon=True).start()


def _persist_token_usage(
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
    project: str,
    caller_name: str = "",
    model: str = "",
    version_id: str = "",
) -> None:
    """★ 持久化单次 AI 调用的 token 用量到 ai_health_metrics。

    - 每笔调用写入 3 行（prompt_tokens / completion_tokens / cost_usd），
      metric_type='token_usage'。
    - ★ v6: details 列附带 caller_name + model，供 Phase 级 Token 统计。
    - /api/token-stats 通过 SUM(value) GROUP BY metric_name 聚合，
      与 _token.py 内存累计值合并得到"合计"行。
    - 后台线程执行，不阻塞 AI 调用链；DB 不可用时静默跳过。
    - project 为空时跳过（FK 约束要求有效 project）。
    """
    if not (prompt_tokens or completion_tokens):
        return

    # ★ 修复：在父线程捕获 _thread_run_id，避免后台线程 thread-local 丢失
    _captured_run_id = ""
    try:
        _captured_run_id = getattr(_st._thread_run_id, "value", "") or ""
    except Exception:
        _log.debug("[Observers] thread_run_id 读取失败", exc_info=True)

    # ★ 根因修复（token_usage 族零记录 · 第二面：项目绑定丢失）：
    #   调用方传的是 _st._current_project()（thread-local）。流水线的 AI 调用发生在
    #   executor/后台线程，thread-local 为空 → 原先 `if not project: return` 直接丢弃，
    #   实测「Step2 做了 11 次真实 AI 调用，token_usage 一行未落」。
    #   修法：与 metrics_collector.record() 的既有兜底同源——
    #     ① run_id 标准格式 {project}__{version_label}（src/core/versioning.py）反解；
    #     ② 再回落 config.project_name（Step4 各插件通用取法）。
    #   仍取不到才跳过（FK 约束要求有效 project）。
    if not project:
        with guard("_persist_token_usage: if _captured_run_id and '__' in _cap", log=_log):
            if _captured_run_id and "__" in _captured_run_id:
                project = str(_captured_run_id).rsplit("__", 1)[0]
    if not project:
        with guard("_persist_token_usage: from src.core.config_loader import c", log=_log):
            from src.core.config_loader import config as _cfg2

            project = getattr(_cfg2, "project_name", "") or ""
    if not project:
        _log.debug("[token-persist] 无有效 project，跳过落库")
        return

    # ★ 根因修复（token_usage 族全库零记录）：
    #   ai_health_metrics.version_id 是 `bigint NOT NULL DEFAULT 0`，
    #   而此处原先直接把入参 version_id（默认 "" 字符串）绑进 INSERT →
    #   MySQL 严格模式报 "Incorrect integer value: ''" → 整批 INSERT 抛错 →
    #   被 except 静默 rollback → **每一次 AI 调用都写不进 token_usage**，
    #   连带 /api/token-stats 跨会话合计恒为 0。
    #   修法：与 metrics_collector.record() 同源——统一用 versioning.current_version_id()
    #   解析（单一事实源，不依赖 thread-local，后台线程同样可用）。
    _resolved_version_id = 0
    try:
        if version_id not in (None, "", 0):
            _resolved_version_id = int(version_id)
    except Exception:
        _resolved_version_id = 0
    if not _resolved_version_id:
        try:
            from src.core.versioning import current_version_id

            _resolved_version_id = int(current_version_id(project) or 0)
        except Exception as _e:
            _log.debug("[token-persist] version_id 解析失败(降级 0): %s", _e)
            _resolved_version_id = 0

    def _write():
        with guard("_write: _st._load_db_token_baseline()", log=_log):
            # ★ 关键：在首次写入 DB 前先加载基线。
            #    因为基线只应包含本次会话之前的历史数据，
            #    所以必须在任何当前会话的 delta 写入 DB 之前完成查询。
            #    基线一旦加载就缓存，后续写入不影响。
            _st._load_db_token_baseline()

            import json as _json

            _details = _json.dumps(
                {
                    "caller_name": caller_name or "",
                    "model": model or "",
                },
                ensure_ascii=False,
            )

            from src.models.base import get_conn

            conn = get_conn()
            try:
                rows = [
                    ("prompt_tokens", int(prompt_tokens or 0)),
                    ("completion_tokens", int(completion_tokens or 0)),
                ]
                if cost and cost > 0:
                    rows.append(("cost_usd", round(float(cost), 6)))
                for metric_name, value in rows:
                    if value:
                        conn.execute(
                            "INSERT INTO ai_health_metrics "
                            "(project, version_id, run_id, step, metric_type, metric_name, category, value, total, details) "
                            "VALUES (%s, %s, %s, 0, 'token_usage', %s, 'token', %s, 0, %s)",
                            (
                                project,
                                _resolved_version_id,
                                _captured_run_id,
                                metric_name,
                                float(value),
                                _details,
                            ),
                        )
                conn.commit()
            except Exception as _ie:
                # ★ 2026-09-23：原为静默 rollback，导致 token_usage 全库零记录且无任何线索。
                #   改为 warning，异常可观测（不改变"不阻塞 AI 调用链"的语义）。
                _log.warning("[token-persist] INSERT 失败(已 rollback): %s", _ie)
                with guard("_write: conn.rollback()", log=_log):
                    conn.rollback()
            finally:
                with guard("_write: conn.close()", log=_log):
                    conn.close()

    threading.Thread(target=_write, daemon=True, name="token-persist").start()


# ═══════════════════════════════════════════════════════════════
# Router 日志劫持 — 检测模型切换
# ═══════════════════════════════════════════════════════════════

_ROUTER_HIJACKED = False


def _hijack_router_logs():
    """劫持 Router 日志，更新 Token 面板的切换原因和模型列。（后台线程，不阻塞启动）"""
    global _ROUTER_HIJACKED
    if _ROUTER_HIJACKED:
        return
    _ROUTER_HIJACKED = True

    def _do_hijack():
        with guard("_do_hijack: from src.ai.router_module import _lo", log=_log, level=logging.WARNING):
            from src.ai.router_module import _log as router_logger

            class _RouterHandler(logging.Handler):
                def emit(self, record):
                    msg = record.getMessage()
                    if "本地" in msg and "成功" in msg:
                        _st._set_token_meta(model="本地", switch_reason="")
                    elif "云端" in msg and "路由成功" in msg:
                        stats = _st._read_token_stats()
                        if stats["last_model"] == "—":
                            _st._set_token_meta(model="云端")

            router_logger.addHandler(_RouterHandler())

            with guard("_do_hijack: from src.ai.router_module import rou", log=_log, level=logging.WARNING):
                from src.ai.router_module import router

                if not router._local_enabled:
                    _st._set_token_meta(model="云端", switch_reason="无GPU")

    threading.Thread(target=_do_hijack, daemon=True, name="router-hijack").start()


# ═══════════════════════════════════════════════════════════════
# 模型切换回调 — 供 browser_executor 调用
# ═══════════════════════════════════════════════════════════════


def log_step_ok(step_num: int, total: int, elapsed: float, desc: str):
    msg = f"##HTML:##<span style='color:#22c55e;font-weight:bold'>  ✓ [Step {step_num}/{total}] OK ({elapsed:.1f}s) — {desc[:60]}</span>"
    _log.info(msg)


def log_step_fail(step_num: int, total: int, elapsed: float, desc: str):
    msg = f"##HTML:##<span style='color:#ef4444;font-weight:bold'>  ✗ [Step {step_num}/{total}] FAIL ({elapsed:.1f}s) — {desc[:60]}</span>"
    _log.info(msg)


def update_monitor_dom(dom_count: int, original_count: int = 0):
    _st._set_token_meta(
        dom_count=dom_count, dom_original=original_count if original_count else None
    )
