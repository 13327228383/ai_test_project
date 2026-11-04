# -*- coding: utf-8 -*-
"""指标页"0/空"诊断：直接调用全部 fetcher，逐板块输出真实返回。

目的：区分「数据缺失（DB 真没有）」vs「查询/渲染缺陷（DB 有但取不到）」。
用户观察到指标页大量 0/空 —— 本脚本给出每个板块的根因归属。
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _empty_score(d, depth=0):
    """粗略判定一个 fetcher 返回是否为空/全 0：返回 (空值键数, 总键数, 样例)"""
    if d is None:
        return (1, 1, "None")
    if isinstance(d, dict):
        none_keys = []
        total = 0
        for k, v in d.items():
            total += 1
            if v is None or v == "" or v == [] or v == {} or v == 0:
                none_keys.append(str(k))
        return (len(none_keys), total, ",".join(none_keys[:12]))
    return (0, 1, str(d)[:60])


def main():
    from src.backend import state as _state
    from src.core.config_loader import set_current_user

    set_current_user("admin")

    fetchers = {}
    from src.backend.routes.metrics_page.fetchers._ai_overall import _fetch_ai_overall
    from src.backend.routes.metrics_page.fetchers._step4_plugins import _fetch_step4_plugins
    from src.backend.routes.metrics_page.fetchers._p0_governance import _fetch_p0_governance
    from src.backend.routes.metrics_page.fetchers._step2_breakdown import _fetch_step2_breakdown
    from src.backend.routes.metrics_page.fetchers._pipeline_ext import _fetch_pipeline_ext
    from src.backend.routes.metrics_page.fetchers._h4_new_metrics import _fetch_h4_new_metrics
    from src.backend.routes.metrics_page.fetchers._fusion import _fetch_fusion
    from src.backend.routes.metrics_page.fetchers._cdp_browser import _fetch_cdp_browser

    fetchers = {
        "ai_overall": _fetch_ai_overall,
        "step4_plugins": _fetch_step4_plugins,
        "p0_governance": _fetch_p0_governance,
        "step2_breakdown": _fetch_step2_breakdown,
        "pipeline_ext": _fetch_pipeline_ext,
        "h4_new_metrics": _fetch_h4_new_metrics,
        "fusion": _fetch_fusion,
        "cdp_browser": _fetch_cdp_browser,
    }

    for project in ("hospital_sut", "modsystem"):
        _state._set_current_project(project)
        print("=" * 72)
        print("PROJECT =", project)
        print("=" * 72)
        for name, fn in fetchers.items():
            try:
                r = fn()
            except Exception as e:
                print(f"  {name:<18} 异常: {type(e).__name__}: {str(e)[:100]}")
                continue
            empty, total, sample = _empty_score(r)
            tag = "全空" if total and empty == total else ("大量空" if total and empty >= total * 0.6 else "有数据")
            print(f"  {name:<18} 空值键 {empty}/{total}  [{tag}]  空: {sample[:100]}")
        print()


if __name__ == "__main__":
    main()
