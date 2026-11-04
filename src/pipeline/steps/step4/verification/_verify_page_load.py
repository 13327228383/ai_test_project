# -*- coding: utf-8 -*-
"""页面加载性能断言 — page_load_time_check。

场景：首屏加载应 < 3s；DOMContentLoaded 应 < 1s；FCP（首次内容绘制）应 < 1.5s。
缺失后果：性能基线规则漏测（response_time_check 只覆盖 API，不覆盖整体页面）。

实现：
  1. 通过 page.evaluate 取 PerformanceNavigationTiming。
  2. 取 loadEventEnd / domContentLoadedEventEnd / responseStart 等时间点。
  3. 按 metric 校验是否 ≤ max_ms。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


# metric 小写键 → JS/PerformanceTiming 使用的规范名
_METRIC_CANON: dict[str, str] = {
    "load": "load",
    "domcontentloaded": "domContentLoaded",
    "fcp": "fcp",
    "ttfb": "ttfb",
}


async def verify_page_load_time(page, check: dict[str, Any]) -> dict[str, Any]:
    """页面加载性能断言。

    check 格式：
      {"type": "page_load_time_check",
       "metric": "load",                              # load / domContentLoaded / fcp / ttfb
       "max_ms": 3000}                                 # 最大允许毫秒数
    """
    try:
        # ★ 根因修复(2026-09-21): 与 _verify_storage 同类缺陷——
        #   metric 先 .lower() 再与含大写的 "domContentLoaded" 比较 → 该分支永假；
        #   且 lower 后的值传给 JS 后与 'domContentLoaded' 严格相等比较也永假 → value 恒为 0。
        #   修法：输入归一化用小写匹配，求值/传参用规范名（单一事实源 _METRIC_CANON）。
        _raw_metric = (check.get("metric") or "load").strip()
        metric = _raw_metric.lower()
        max_ms = int(check.get("max_ms") or 5000)

        if metric not in _METRIC_CANON:
            return {
                "passed": False,
                "evidence": "[PageLoadCheck] 未知 metric: %s" % _raw_metric,
            }
        canon_metric = _METRIC_CANON[metric]

        # 通过 PerformanceNavigationTiming + PerformancePaint 取时间
        js = """
        (metric) => {
            const nav = performance.getEntriesByType('navigation')[0];
            if (!nav) return {error: 'no navigation timing'};
            let value = 0;
            if (metric === 'load') value = nav.loadEventEnd - nav.startTime;
            else if (metric === 'domContentLoaded') value = nav.domContentLoadedEventEnd - nav.startTime;
            else if (metric === 'ttfb') value = nav.responseStart - nav.startTime;
            else if (metric === 'fcp') {
                const paints = performance.getEntriesByType('paint');
                const fcp = paints.find(p => p.name === 'first-contentful-paint');
                if (!fcp) return {error: 'no fcp'};
                value = fcp.startTime;
            }
            return {value: Math.round(value)};
        }
        """
        try:
            result = await page.evaluate(js, {"metric": canon_metric})
        except Exception as e:
            return {
                "passed": False,
                "evidence": "[PageLoadCheck] evaluate 失败: %s" % str(e)[:60],
            }

        if not isinstance(result, dict) or "error" in result:
            return {
                "passed": False,
                "evidence": "[PageLoadCheck] 无法获取 %s: %s"
                % (metric, result.get("error", "未知")),
            }

        actual_ms = int(result.get("value", -1))
        if actual_ms < 0:
            return {
                "passed": False,
                "evidence": "[PageLoadCheck] %s 取值异常: %d" % (metric, actual_ms),
            }

        passed = actual_ms <= max_ms
        return {
            "passed": passed,
            "evidence": "[PageLoadCheck] metric=%s → %dms (阈值 %dms) → %s"
            % (metric, actual_ms, max_ms, "PASS" if passed else "FAIL"),
        }
    except Exception as e:
        _log.error("[PageLoadCheck] 异常: %s", str(e)[:120], exc_info=True)
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] page_load_time_check 崩溃: %s" % str(e)[:80],
        }
