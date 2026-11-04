# -*- coding: utf-8 -*-
"""
health.fetchers — 健康度仪表盘数据获取与格式化模块

将原 health.py 中的辅助函数按职责拆分为：
- _queries.py — 数据库查询逻辑（_get_conn, _latest_run_id, _query_metrics, _integrate_metrics）
- _format.py  — 格式化与标签构建（_confidence, _build_labels, _build_summary）
"""
