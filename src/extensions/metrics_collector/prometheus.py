# -*- coding: utf-8 -*-
"""Prometheus 指标导出 mixin — 从 metrics_collector.py 拆出，零业务逻辑变更。"""

import logging

_log = logging.getLogger(__name__)

try:
    from prometheus_client import (
        Counter,
        Histogram,
        Gauge,
        CollectorRegistry,
        generate_latest,
    )

    _PROMETHEUS_AVAILABLE = True
except Exception:
    _PROMETHEUS_AVAILABLE = False


class _PrometheusStub:
    """Prometheus 不可用时，提供相同接口的 no-op 占位类，避免到处 if。"""

    def __init__(self, *args, **kwargs):
        pass

    def observe(self, value):
        pass

    def inc(self, amount=1):
        pass

    def set(self, value):
        pass

    def labels(self, *args, **kwargs):
        return self


class _PrometheusMixin:
    """Prometheus 指标对象初始化 + observe/record/get 接口。"""

    def _init_prometheus(self):
        """初始化 Prometheus 指标对象（线程安全）。"""
        if _PROMETHEUS_AVAILABLE:
            self._registry = CollectorRegistry()
            api_buckets = [
                0.005,
                0.01,
                0.025,
                0.05,
                0.1,
                0.25,
                0.5,
                1.0,
                2.5,
                5.0,
                10.0,
            ]
            db_buckets = [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5]
            pl_buckets = [1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0]
            self._api_request_duration = Histogram(
                "api_request_duration_seconds",
                "API 请求耗时（秒）",
                ["method", "endpoint", "status_code"],
                registry=self._registry,
                buckets=api_buckets,
            )
            self._db_query_duration = Histogram(
                "db_query_duration_seconds",
                "DB 查询耗时（秒）",
                ["operation", "table"],
                registry=self._registry,
                buckets=db_buckets,
            )
            self._pipeline_step_duration = Histogram(
                "pipeline_step_duration_seconds",
                "流水线步骤耗时（秒）",
                ["step", "project"],
                registry=self._registry,
                buckets=pl_buckets,
            )
            self._external_dependency_up = Gauge(
                "external_dependency_up",
                "外部依赖健康状态（1=up, 0=down）",
                ["name"],
                registry=self._registry,
            )
            self._ai_token_total = Counter(
                "ai_token_consumption_total",
                "AI Token 消耗总量",
                ["model", "provider", "type"],
                registry=self._registry,
            )
            self._ai_invocation_total = Counter(
                "ai_model_invocation_total",
                "AI 模型调用次数",
                ["model", "provider", "status"],
                registry=self._registry,
            )
            self._pipeline_step_total = Counter(
                "pipeline_step_execution_total",
                "流水线步骤执行次数",
                ["step", "project", "status"],
                registry=self._registry,
            )
        else:
            self._registry = None
            self._api_request_duration = _PrometheusStub()
            self._db_query_duration = _PrometheusStub()
            self._pipeline_step_duration = _PrometheusStub()
            self._external_dependency_up = _PrometheusStub()
            self._ai_token_total = _PrometheusStub()
            self._ai_invocation_total = _PrometheusStub()
            self._pipeline_step_total = _PrometheusStub()

    def observe_api_request(
        self, method: str, endpoint: str, status_code: int, duration_sec: float
    ):
        """记录一次 API 请求耗时。"""
        try:
            self._api_request_duration.labels(
                method=method, endpoint=endpoint, status_code=str(status_code)
            ).observe(duration_sec)
        except Exception as _pass_e:
            _log.warning("非关键异常: %s", _pass_e, exc_info=True)

    def observe_db_query(self, operation: str, table: str, duration_sec: float):
        """记录一次 DB 查询耗时。"""
        try:
            self._db_query_duration.labels(operation=operation, table=table).observe(
                duration_sec
            )
        except Exception as _pass_e:
            _log.warning("非关键异常: %s", _pass_e, exc_info=True)

    def observe_pipeline_step(self, step: str, project: str, duration_sec: float):
        """记录一次流水线步骤耗时。"""
        try:
            self._pipeline_step_duration.labels(
                step=str(step), project=project
            ).observe(duration_sec)
        except Exception as _pass_e:
            _log.warning("非关键异常: %s", _pass_e, exc_info=True)

    def record_token_consumption(
        self, model: str, provider: str, token_type: str, amount: int
    ):
        """记录 AI Token 消耗。token_type: input / output。"""
        try:
            self._ai_token_total.labels(
                model=model, provider=provider, type=token_type
            ).inc(amount)
        except Exception as _pass_e:
            _log.warning("非关键异常: %s", _pass_e, exc_info=True)

    def record_ai_invocation(self, model: str, provider: str, status: str = "success"):
        """记录一次 AI 模型调用。status: success / failed。"""
        try:
            self._ai_invocation_total.labels(
                model=model, provider=provider, status=status
            ).inc()
        except Exception as _pass_e:
            _log.warning("非关键异常: %s", _pass_e, exc_info=True)

    def record_pipeline_step_result(
        self, step: str, project: str, status: str = "success"
    ):
        """记录一次流水线步骤执行结果。"""
        try:
            self._pipeline_step_total.labels(
                step=str(step), project=project, status=status
            ).inc()
        except Exception as _pass_e:
            _log.warning("非关键异常: %s", _pass_e, exc_info=True)

    def set_dependency_health(self, name: str, up: bool):
        """设置外部依赖健康状态（1=up, 0=down）。"""
        try:
            self._external_dependency_up.labels(name=name).set(1 if up else 0)
        except Exception as _pass_e:
            _log.warning("非关键异常: %s", _pass_e, exc_info=True)

    def get_prometheus_metrics(self) -> bytes:
        """返回 Prometheus 文本格式的指标数据。"""
        if _PROMETHEUS_AVAILABLE and self._registry is not None:
            return generate_latest(self._registry)
        return b""

    def is_prometheus_available(self) -> bool:
        return _PROMETHEUS_AVAILABLE
