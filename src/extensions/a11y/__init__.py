# -*- coding: utf-8 -*-
"""可访问性（a11y）检查能力 — 通用规则驱动，不依赖任何靶子系统。

与 security_scanner / perf_baseline 并列的扩展能力（高级 SDET 维度）。
纯规则确定性输出（fail-closed 哲学），输入任意 HTML，输出结构化发现。

用法::

    from src.extensions.a11y import run_a11y_check
    result = run_a11y_check("<html>...</html>", source="spa_demo/index.html")

[已复核-DeepSeek-V3 2026-08-16]
"""

from src.extensions.a11y.checker import (
    A11yFinding,
    SEVERITY_ORDER,
    analyze_html,
    run_a11y_check,
)

__all__ = ["A11yFinding", "SEVERITY_ORDER", "analyze_html", "run_a11y_check"]
