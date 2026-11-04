# -*- coding: utf-8 -*-
"""
metrics_page.fetchers — 指标中心各领域数据获取器

将原 metrics_page.py 中 7 大领域的 _fetch_* 函数拆分为独立模块：
- _health.py      — 健康度汇总（从 health.py 接口获取）
- _ai_overall.py  — AI 综合健康（Flaky/幻觉分类）
- _fusion.py      — 融合架构（Tool-mode/Recipe缓存/原子回滚）
- _step4_plugins.py — Step4 基础插件（复用 data.py）
- _p0_governance.py — P0 治理（Harness/Scorecard/TokenBudget/FlakyGuard）
- _pipeline_ext.py  — 流水线扩展（Step1/2/3/5 插件注册数）
- _system.py      — 系统运行时（psutil 进程/内存/CPU）
- _smart.py       — 智能能力引擎（业务能力/DOM/区域树/模糊匹配）
- _utils.py       — 共享工具：_db_conn / _current_project_id / _num
"""
