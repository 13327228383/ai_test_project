# -*- coding: utf-8 -*-
"""
health/__init__.py — 健康度仪表盘 API 包入口
=============================================
原 health.py 按功能域拆分为：
- health.py             — Blueprint + 常量（facade 主体）
- routes_summary.py     — /api/health/summary
- routes_trends.py      — /api/health/trends, /api/health/latest-run
- routes_diagnostics.py — /api/health/detailed, /api/health/metrics, /healthz, /readyz
- fetchers/             — 查询逻辑与格式化辅助

确保已有 import 路径（如 from src.backend.routes.health import health_bp）零变更即可继续工作。
"""

from .health import (
    health_bp,
    _get_conn,
    _latest_run_id,
)

# ── 触发路由自注册（按加载顺序，与原 health.py 路由顺序一致） ──
from . import routes_summary  # noqa: E402, F401 — /api/health/summary
from . import routes_trends  # noqa: E402, F401 — /trends, /latest-run
from . import (
    routes_diagnostics,
)  # noqa: E402, F401 — /detailed, /metrics, /healthz, /readyz

from .routes_summary import api_health_summary  # noqa: E402, F401 — 兼容 re-export

__all__ = ["health_bp", "api_health_summary", "_get_conn", "_latest_run_id"]
