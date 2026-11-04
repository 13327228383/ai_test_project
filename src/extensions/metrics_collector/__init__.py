# -*- coding: utf-8 -*-
"""
AI 健康度指标收集器 (MetricsCollector)
======================================
单例模式，即时写入 MySQL（immediate=True 降级到缓冲）。
在流水线各步骤埋点收集幻觉率、守卫拦截、校验不一致等指标，
每次 record() 默认直接写入 MySQL，写入失败则降级到内存缓冲，
运行结束时通过 executor.py 的 finally 钩子 flush_run() 兜底。

扩展：新增 Prometheus 指标导出（线程安全，优雅降级）。

【2026-06-18 重构】原 480 行单文件按职责拆分：
- prometheus.py  — Prometheus 导出（observe_*/record_token_consumption/...)
- health.py      — 核心 record() + 健康度领域埋点（coverage/cross_check/verification/...)
- plugins.py     — 新插件指标（capability/fuzzy_match/dom_sizer/region_tree/click_fallback/dom_diff）
- persistence.py — MySQL 写入 + 缓冲管理 + 置信度趋势 JSONL
"""

import threading, atexit
import logging

_log = logging.getLogger(__name__)

# ── 从拆分子模块 import ──
from .prometheus import _PrometheusMixin, _PrometheusStub, _PROMETHEUS_AVAILABLE
from .health import _RecordHealthMixin
from .plugins import _PluginsMixin
from .persistence import _PersistenceMixin


class MetricsCollector(
    _PrometheusMixin, _RecordHealthMixin, _PluginsMixin, _PersistenceMixin
):
    """指标收集器。线程安全，所有埋点调用 try/except 包裹。

    ★ 方案C 改造：移除 __new__ 强制单例，允许 PerProjectRegistry 创建多实例。
    """

    _lock = threading.Lock()

    def __init__(self):
        self._buffer: dict[str, list[dict]] = {}
        self._buffer_lock = threading.Lock()
        self._confidence_history: list[dict] = []
        self._confidence_lock = threading.Lock()
        self._confidence_history_loaded = False
        self._confidence_file_cache: str = ""
        self._init_prometheus()


# ★ 方案C 改造：metrics_collector 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

metrics_collector = PerProjectRegistry(MetricsCollector)


def record_metric(metric_fn_name: str, extra_kwargs: dict | None = None) -> None:
    """按当前线程 run_id 记录指标（唯一事实源）。

    历史上 browser_ai / 工具模式 / 文本模式 / 校验器 / 动作分发器各自手抄了
    同一份实现（5 处），语义完全一致且都不带业务规则。现统一收敛到本函数：
    调用方 ``from src.extensions.metrics_collector import record_metric`` 即可，
    不再各自维护副本。异常仅 debug 日志，不影响主流程。
    """
    from src.core.guarded import guard

    with guard("record_metric: " + metric_fn_name, log=_log):
        from src.backend.state import _thread_run_id
        from src.core.config_loader import config

        rid = getattr(_thread_run_id, "value", "") or ""
        if rid:
            fn = getattr(metrics_collector, metric_fn_name, None)
            if fn:
                kwargs = {"project": config.project_name, "run_id": rid}
                if extra_kwargs:
                    kwargs.update(extra_kwargs)
                fn(**kwargs)


def _atexit_flush():
    # ★ 方案C 修复：atexit 时无 project 上下文，必须用 call_all 遍历所有实例，
    #   否则只有 _default 实例的缓冲被 flush，其他 project 的指标数据会丢失
    try:
        metrics_collector.call_all("flush_all")
    except Exception as _pass_e:
        _log.warning("非关键异常: %s", _pass_e, exc_info=True)


atexit.register(_atexit_flush)
