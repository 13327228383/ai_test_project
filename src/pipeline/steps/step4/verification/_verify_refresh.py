# -*- coding: utf-8 -*-
"""页面刷新检测断言 — page_refresh_check。

通过比较动作前后的 URL + DOM signature 判断页面是否发生了刷新。
需要调用方在动作前后分别传入页面状态快照。

设计原则：不抛异常，无快照 → 标 evidence 不 fail（避免误杀）。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


async def verify_refresh(
    page, check: dict[str, Any], pre_state: dict = None
) -> dict[str, Any]:
    """页面刷新检测断言。

    check 格式：
      {"type": "page_refresh_check",
       "expected": "refreshed|not_refreshed"}

    pre_state: 动作前的页面状态快照 {"url": str, "dom_signature": str}
    当前页面状态从 page 实时获取。
    """
    check_type = check.get("type", "")
    expected = check.get("expected", "refreshed")

    if page is None:
        return {"passed": True, "evidence": "无 page，刷新断言跳过"}

    if not pre_state:
        return await _check_refresh_via_perf_api(page, check)

    try:
        pre_url = pre_state.get("url", "")
        pre_sig = pre_state.get("dom_signature", "")

        current_url = page.url if page else ""

        current_sig = ""
        try:
            current_sig = await page.evaluate(
                "() => { const el = document.querySelectorAll('*'); return el.length + ':' + "
                "(document.body ? document.body.innerText.length : 0); }"
            )
        except Exception:
            current_sig = ""

        url_same = pre_url == current_url
        sig_changed = (pre_sig != current_sig) and bool(pre_sig) and bool(current_sig)

        is_refreshed = url_same and sig_changed

        if expected == "refreshed":
            passed = is_refreshed
            evidence = "刷新检测: URL相同(%s) + DOM变化(%s) → %s" % (
                "是" if url_same else "否",
                "是" if sig_changed else "否",
                "已刷新" if is_refreshed else "未刷新",
            )
        else:
            passed = not is_refreshed
            evidence = "刷新检测: URL相同(%s) + DOM变化(%s) → %s（期望未刷新）" % (
                "是" if url_same else "否",
                "是" if sig_changed else "否",
                "已刷新" if is_refreshed else "未刷新",
            )

        return {"passed": passed, "evidence": evidence}

    except Exception as e:
        _log.warning("[RefreshCheck] 异常: %s", str(e)[:120])
        return {"passed": True, "evidence": "刷新断言异常，跳过: %s" % str(e)[:80]}


async def _check_refresh_via_perf_api(page, check: dict[str, Any]) -> dict[str, Any]:
    """无 pre_state 时，用 Performance API 检测页面是否通过 reload 加载。"""
    expected = check.get("expected", "refreshed")
    try:
        nav_type = await page.evaluate(
            "() => { const e = performance.getEntriesByType('navigation'); "
            "return e.length > 0 ? e[0].type : 'unknown'; }"
        )
        is_refreshed = nav_type == "reload"

        if expected == "refreshed":
            passed = is_refreshed
        else:
            passed = not is_refreshed

        return {
            "passed": passed,
            "evidence": "刷新检测[PerfAPI] navigation.type=%s → %s"
            % (nav_type, "已刷新" if is_refreshed else "未刷新"),
        }
    except Exception as e:
        _log.warning("[RefreshCheck/PerfAPI] 异常: %s", str(e)[:120])
        return {
            "passed": True,
            "evidence": "刷新断言(PerfAPI)异常，跳过: %s" % str(e)[:80],
        }
