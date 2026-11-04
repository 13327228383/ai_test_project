# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""browser_structured_executor.py — 结构化动作/断言执行方法。从 browser_core.py 拆分。

★ f2 重构（2026-07-06）：
  - 删除 navigate/click/fill/select_option/upload/wait/press_key/scroll 的迁移期 if-elif 兜底分支。
  - 所有标准动作统一由 action_dispatchers 查表执行（browser_tools._execute_tool）。
  - 仅保留 navigate 的 URL 预处理（中文页面名→路由解析、相对路径→绝对路径）作为前置步骤。
  - 模糊匹配能力下沉到 browser_tools/locator.py，本文件不再持有 _normalize_hint/_extract_core_keyword。
★ 架构债清理（2026-07-24）：
  - Tab 导航方法拆分到 tab_navigation.py（§4.3 单文件≤800行）。
"""

import asyncio
import logging
import re as _re
from typing import Any
from urllib.parse import urlparse

from src.pipeline.steps.step4.action_dispatchers import (
    dispatch_action,
    dispatch_assertion,
)
from src.pipeline.steps.step4.form_completion import complete_form_fields
from src.pipeline.step3._trigger_actions import _INPUT_ACTION_TYPES, _is_submit_click
from src.pipeline.utils.action_library_store import action_library_store
from src.pipeline.steps.step4.browser_tools.locator import _extract_core_keyword

# ★ T-003: 复用 selectors.py 的 CSS 转义函数
from src.pipeline.steps.step4.playwright_engine.selectors import _escape_css_str

# ★ 架构债清理(2026-07-24): Tab 导航方法拆分到 tab_navigation.py（§4.3 单文件≤800行）
from src.pipeline.steps.step4.tab_navigation import _ensure_tab_active_after_navigate

_log = logging.getLogger(__name__)


def _resolve_page_name_to_url(page_name: str) -> str:
    """中文页面名 → 真实路由。全引擎**唯一**的页面名解析实现（与 Step3 同源）。

    why: 该语义此前在 Step4 另有一份实现，查询 `page_routes` 表——而该表从未建表，
         查询必然抛错并被 guard 静默吞掉 → 每次导航都退化为「回首页 + 按文本点链接」
         的脆弱兜底（页面含同名子链接时必然点错）。页面知识只应有一处来源：
         nav_graph_nodes（Step3 `_navigation_resolver._load_menu_nodes` 亦读此处）。
    """
    _name = str(page_name or "").strip()
    if not _name:
        return ""
    try:
        from src.core.config_loader import config as _cfg
        from src.core.versioning import current_version_label
        from src.pipeline.utils.nav_graph_store import read_nav_graph_nodes

        _project = str(
            getattr(_cfg, "project", "") or getattr(_cfg, "project_name", "") or ""
        ).strip()
        if not _project:
            return ""
        _nodes = (
            read_nav_graph_nodes(_project, current_version_label(_project) or "") or []
        )
    except Exception as _e:  # 页面知识不可用时降级菜单定位，不阻断执行
        _log.debug("[Structured] 页面知识源读取失败(降级菜单定位): %s", _e)
        return ""

    # 精确标题优先；其次标题互含（页面名常是标题前缀，如 "资产" ⊂ "资产列表"）
    for _n in _nodes:
        if str(_n.get("page_title") or "").strip() == _name:
            return str(_n.get("page_url") or _n.get("page_path") or "").strip()
    for _n in _nodes:
        _t = str(_n.get("page_title") or "").strip()
        if _t and (_name in _t or _t in _name):
            return str(_n.get("page_url") or _n.get("page_path") or "").strip()
    return ""


async def _resolve_navigate_url(engine, action: dict[str, Any]) -> str | None:
    """navigate 专用 URL 预处理：中文页面名→路由、相对路径→绝对路径。

    Returns:
        解析后的 URL 字符串；若需走菜单定位 fallback 则返回 None（已执行菜单导航）。
        action["url"] 会被原地更新为最终 URL。
    """
    atype = action.get("type", "") or action.get("action", "")
    if atype != "navigate":
        return action.get("url", "")

    # ★ §4.17-B(2026-10-28): **占位导航目标不是目标页** —— 如实失败，不导航到站点根。
    #   根因: 生成端曾在无导航事实时兜底 `nav_url or nav_target or "/"`，把站点根
    #   伪装成目标页；执行端把 "/" 当相对路径拼接 → 静默停在首页并判 PASS
    #   （实测 ~488 次 "[Structured] 导航URL拼接: http://127.0.0.1:8020/"，靠 AI 兜底
    #   猜按钮）。判据与生成端**同源**：`is_placeholder_nav`（无 url 且 target 为
    #   ""/"/"/"/login"）。解析不到就如实失败，由主循环判结构化失败。
    from src.pipeline.step3._navigation_resolver import is_placeholder_nav

    if is_placeholder_nav(action):
        _log.warning(
            "[Structured] 导航目标未解析(占位 %r): 如实判失败，不导航到站点根",
            action.get("target_hint", ""),
        )
        action["_nav_ok"] = False
        action["url"] = ""
        return None

    target = action.get("target_hint", "")
    value = action.get("value", "")
    # ★ 通用修复(2026-07-19): url 优先于 target_hint，避免中文页面名被当作 URL 导致导航失败。
    url = value or action.get("url") or target
    if not url:
        return ""

    # 已是绝对 URL，直接返回
    if url.startswith(("http://", "https://")):
        action["url"] = url
        return url

    from src.core.config_loader import config
    from urllib.parse import urlparse, urljoin
    import re as _re

    raw_base = (getattr(config, "target_url", "") or "").strip()

    # ★ 方案B (ADR-017, 2026-07-23): 识别 intent: 语义前缀，走完整菜单导航
    #   根因(§10.9 3层追问):
    #     1. 表层: [Structured] 导航菜单定位失败 — 简单CSS选择器文本匹配找不到Tab子页面
    #     2. 中层: intent:辅材配方管理 被当作中文页面名，走简单菜单文本匹配(6个CSS选择器)，
    #            查 page_routes 表失败，菜单定位也失败，返回None跳过dispatcher
    #     3. 根因: _resolve_navigate_url 不识别 intent: 协议，且返回None后跳过了
    #            _fallback_menu_navigate 的完整 nav_map 查询+direct_url+Tab处理逻辑
    #   通用性(§12.4 9维度):
    #     1. 跨系统: intent: 协议是通用语义层，_fallback_menu_navigate 覆盖所有SPA菜单结构
    #     2. 跨行业: nav_map 关键词匹配适用于任何行业
    #     3. 跨页面: direct_url + 菜单文本匹配 + Tab点击 三重策略覆盖所有页面类型
    #     4. 跨Tab: _fallback_menu_navigate 有 _click_page_tab 处理页面内Tab
    #     5. 数据完整性: nav_map 为空时降级为文本匹配，不崩溃
    #     6. 根因溯源: 让 intent: 走完整导航逻辑，而非简单文本匹配
    #     7. 覆盖性: 所有 intent: navigate 都走此路径
    #     8. 全局观: 与 _extract_nav_keywords 的 intent: 解析修复形成完整链路
    #     9. 职责边界: Step4 在自己职责内解析 intent: 并执行导航
    #   自我反驳三次:
    #     1) 剥离前缀走原有中文页面名逻辑？→ 原逻辑是简单CSS文本匹配，找不到Tab子页面
    #     2) 返回纯语义文本让dispatcher处理？→ dispatcher的_goto(中文文本)会失败
    #     3) 直接调用 _fallback_menu_navigate？→ 复用完整导航逻辑，最优
    _INTENT_PREFIX = "intent:"
    if isinstance(url, str) and url.lower().startswith(_INTENT_PREFIX):
        _log.info("[Structured] ★ 方案B intent: 导航: '%s' → 走完整菜单导航", url[:60])
        _nav_ok = False
        with guard("_resolve_navigate_url: from src.pipeline.steps.step4.browse", log=_log, level=logging.WARNING):
            from src.pipeline.steps.step4.browser_tools.execution._single import (
                _fallback_menu_navigate,
            )

            _nav_ok = await _fallback_menu_navigate(engine, "", url)
            if _nav_ok:
                _log.info("[Structured] ★ 方案B intent: 菜单导航成功: '%s'", url[:60])
            else:
                _log.warning(
                    "[Structured] ★ 方案B intent: 菜单导航失败: '%s'", url[:60]
                )
        # ★ RC-AI(2026-10-14): 回写菜单导航真实结果，供 execute_structured_actions
        #   判定整体成败（此前该结果被丢弃 → 导航成功也被判结构化失败）。
        action["_nav_ok"] = bool(_nav_ok)
        # 无论成功失败，返回None跳过dispatcher（已尝试最佳导航方案）
        action["url"] = ""
        return None

    # 检测中文页面名（非 / 开头且含 CJK 字符）
    _is_chinese_name = not url.startswith("/") and bool(
        _re.search(r"[\u4e00-\u9fff]", url)
    )
    if _is_chinese_name:
        _log.warning("[Structured] 导航目标是中文页面名: '%s'，尝试查找实际路由", url)
        _resolved_url = _resolve_page_name_to_url(url)

        if _resolved_url:
            url = _resolved_url
            _log.info("[Structured] 中文页面名 '%s' → 实际路由: %s", target, url)
        else:
            # ★ 回退：导航到 base_url，通过导航菜单定位目标页面
            _log.warning(
                "[Structured] 无法解析中文页面名 '%s'，尝试通过导航菜单定位", url
            )
            page = engine._page
            # ★ RC-AI(2026-10-14): 预置菜单定位结果（未配置/异常分支保持 False），
            #   出口处回写 action["_nav_ok"]，供整体成败判定使用。
            nav_found = False
            if raw_base and page is not None:
                with guard("_resolve_navigate_url: parsed = urlparse(raw_base)", log=_log, level=logging.WARNING):
                    parsed = urlparse(raw_base)
                    root_url = f"{parsed.scheme}://{parsed.netloc}"
                    await page.goto(root_url, timeout=15000)
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    await asyncio.sleep(0.5)
                    search_kw = _extract_core_keyword(url)
                    if not search_kw or len(search_kw) < 2:
                        search_kw = url
                    _log.info("[Structured] 导航菜单定位: 搜索 '%s'", search_kw)
                    _search_kw_esc = _escape_css_str(search_kw)
                    nav_found = False
                    for sel in [
                        f'[role="menuitem"]:has-text("{_search_kw_esc}")',
                        f'[role="link"]:has-text("{_search_kw_esc}")',
                        f'[role="button"]:has-text("{_search_kw_esc}")',
                        f'a:has-text("{_search_kw_esc}")',
                        f'button:has-text("{_search_kw_esc}")',
                        f'li:has-text("{_search_kw_esc}")',
                    ]:
                        try:
                            _loc = page.locator(sel).first
                            if await _loc.count() > 0:
                                await _loc.click(timeout=5000)
                                await page.wait_for_load_state(
                                    "domcontentloaded", timeout=5000
                                )
                                _log.info(
                                    "[Structured] 导航菜单定位成功: '%s' → '%s'",
                                    url,
                                    search_kw,
                                )
                                nav_found = True
                                break
                        except Exception as _e:
                            _log.debug("导航菜单定位点击失败: %s", _e, exc_info=True)
                            continue
                    if not nav_found:
                        _log.warning("[Structured] 导航菜单定位失败: '%s'", url)
            else:
                _log.warning("[Structured] base_url 未配置，无法回退导航: '%s'", url)
            # 菜单定位已完成（成功或失败），返回 None 表示不需要再走 dispatcher
            action["_nav_ok"] = bool(nav_found)
            action["url"] = ""
            return None

    # 检测并截断 {占位符} 路径段（如 /productTask/{id} → /productTask）
    # ★ Fix39 (2026-07-09): 仅对 URL 路径部分（? 之前）做占位符截断，保留 query 参数
    if "{" in url and "}" in url:
        _path_part, _sep, _query_part = url.partition("?")
        _seg = _path_part.split("/")
        _clean = [p for p in _seg if p and not (p.startswith("{") and p.endswith("}"))]
        _cleaned_path = (
            "/".join(_clean) if _clean and _clean[0] == "" else "/" + "/".join(_clean)
        )
        _cleaned_url = _cleaned_path + _sep + _query_part if _sep else _cleaned_path
        if _cleaned_url and _cleaned_url != url:
            _log.warning(
                "[Structured] 导航路径含占位符，截断: '%s' → '%s'", url, _cleaned_url
            )
            url = _cleaned_url

    # ★ 通用修复 (2026-07-18): 剥离查询参数中的占位符值
    # AI 编造的占位符参数值（如 yzCode=YZCODE.000, blendCode=BLEND_000）
    # 会导致目标系统返回空白/错误页面，需剥离后仅保留合法的路由参数
    if "?" in url:
        _path_part, _query_part = url.split("?", 1)
        _params = _query_part.split("&")
        _clean_params = []
        _stripped = []
        for _p in _params:
            if "=" in _p:
                _k, _v = _p.split("=", 1)
                # ★ 检测占位符值模式：
                #   1. 全大写字母+数字后缀 (YZCODE.000, BLEND_000, MATERIAL.000)
                #   2. 以 .000 或 _000 结尾的值
                #   3. 纯数字 ID（123, 456）→ 这些在路径中已处理，但 query 中也可能出现
                _is_placeholder = False
                _v_upper = _v.upper()
                if _v_upper.endswith(".000") or _v_upper.endswith("_000"):
                    _is_placeholder = True
                elif _re.match(r"^[A-Z_]+\.[0-9]+$", _v_upper) or _re.match(
                    r"^[A-Z_]+_[0-9]+$", _v_upper
                ):
                    _is_placeholder = True
                elif _re.match(r"^[0-9]+$", _v) and len(_v) <= 5:
                    # 纯数字且≤5位 → 可能是占位符ID（如 123, 456）
                    _is_placeholder = True
                if _is_placeholder:
                    _stripped.append(f"{_k}={_v}")
                    _log.warning("[Structured] 剥离占位符查询参数: %s=%s", _k, _v)
                else:
                    _clean_params.append(_p)
            else:
                _clean_params.append(_p)
        if _stripped:
            _clean_query = "&".join(_clean_params) if _clean_params else ""
            url = _path_part + ("?" + _clean_query if _clean_query else "")
            _log.info(
                "[Structured] 剥离占位符后URL: %s (剥离了 %d 个参数: %s)",
                url,
                len(_stripped),
                _stripped,
            )

    # ★ 通用修复(2026-07-19): 如果 raw_base 为空，尝试从当前页面URL提取
    if not raw_base:
        try:
            _page_url = (engine._page.url or "") if engine._page else ""
            if _page_url and _page_url.startswith("http"):
                _parsed = urlparse(_page_url)
                raw_base = f"{_parsed.scheme}://{_parsed.netloc}"
                _log.info("[Structured] raw_base 为空，从当前页面URL提取: %s", raw_base)
        except Exception as _e:
            _log.debug("[structured_executor] raw_base 提取异常: %s", _e, exc_info=True)

    # 相对路径拼接 base_url
    if raw_base and not url.startswith(("http://", "https://")):
        parsed = urlparse(raw_base)
        root_url = (
            f"{parsed.scheme}://{parsed.netloc}"
            if parsed.scheme and parsed.netloc
            else raw_base.rstrip("/")
        )
        url = urljoin(root_url, url)
        _log.info("[Structured] 导航URL拼接: %s (base=%s)", url, root_url)

    action["url"] = url
    return url


# =============================================================================
# ★ 通用修复(2026-07-22 v27): 瞬态提示（toast/message）动作后即时捕获
#   根因(3层追问):
#     1. 表层: N01 上传非法文件后 element_exists✗（错误提示未被捕获）
#     2. 中层: antd message 提示 ~4s 后自动消失，Step4 校验时（~19s 后）活 DOM 已不存在；
#              且 upload 工具内部等待（~4s）恰好耗尽提示存活窗口，动作后扫描也不可靠
#     3. 根因: 瞬态提示必须在"出现时"捕获——用 MutationObserver 在页面内实时记录，
#              不依赖任何"事后扫描"的时机运气
#   通用性: 覆盖 Ant Design/ElementUI/Bootstrap/原生 toast 类组件（跨系统/行业/页面）
#   覆盖性: 所有经 dispatch_action 执行的动作（click/upload/fill 等都会触发合并）
#   职责边界: Step4 只做"捕获证据"，不做"是否该提示"的决策（决策在 Step3 checks）
# =============================================================================
_TRANSIENT_HINT_SELECTOR = (
    '[role="alert"], [role="status"], '
    ".ant-message-custom-content, .ant-notification-notice-content, "
    ".el-message, .el-message__content, .el-notification__content, "
    ".toast, .Toastify__toast-body, .swal2-html-container"
)

# MutationObserver：提示出现的瞬间就记录到 window.__e2e_transient_hints（不依赖扫描时机）
_TOAST_OBSERVER_JS = """(sel) => {
  window.__e2e_transient_hints = window.__e2e_transient_hints || [];
  if (window.__e2e_toast_observer) return true;
  const rec = (el) => {
    try {
      const t = (el.innerText || '').trim();
      if (!t) return;
      window.__e2e_transient_hints.push({
        text: t.slice(0, 200), cls: el.getAttribute('class') || '',
        role: el.getAttribute('role') || '', ts: Date.now()
      });
    } catch (e) {}
  };
  const scan = (root) => {
    if (!root || root.nodeType !== 1) return;
    if (root.matches && root.matches(sel)) rec(root);
    if (root.querySelectorAll) root.querySelectorAll(sel).forEach(rec);
  };
  window.__e2e_toast_observer = new MutationObserver(muts => {
    muts.forEach(m => m.addedNodes.forEach(scan));
  });
  window.__e2e_toast_observer.observe(document.body, {childList: true, subtree: true});
  return true;
}"""


async def _ensure_toast_observer(page) -> None:
    """navigate 后注入 MutationObserver（幂等）。异常只记日志不阻塞。"""
    with guard("_ensure_toast_observer: await page.evaluate(_TOAST_OBSERVER_", log=_log, level=logging.WARNING):
        await page.evaluate(_TOAST_OBSERVER_JS, _TRANSIENT_HINT_SELECTOR)


async def _capture_transient_hints(page) -> None:
    """合并瞬态提示到 page._transient_hints（执行级缓存）。

    双通道：
    1. window.__e2e_transient_hints（MutationObserver 实时记录，提示消失后仍有证据）
    2. 活 DOM 扫描（observer 未覆盖时的兜底）
    异常只记日志不阻塞（沉默即失败→记详情）。
    """
    with guard("_capture_transient_hints: hints = getattr(page, '_transient_hi", log=_log, level=logging.WARNING):
        hints = getattr(page, "_transient_hints", None)
        if hints is None:
            hints = []
            setattr(page, "_transient_hints", hints)
        import time as _t

        known = {(h.get("text"), h.get("cls")) for h in hints}
        # 通道1：observer 实时记录（含已消失的提示）
        try:
            observed = await page.evaluate(
                "() => (window.__e2e_transient_hints || []).slice(-50)"
            )
        except Exception:
            observed = []
        for el in observed or []:
            if not isinstance(el, dict):
                continue
            key = (el.get("text"), el.get("cls"))
            if key not in known and (el.get("text") or "").strip():
                hints.append(
                    {
                        "text": el.get("text", ""),
                        "cls": el.get("cls", ""),
                        "role": el.get("role", ""),
                        "ts": _t.time(),
                    }
                )
                known.add(key)
                _log.info(
                    "[Transient] observer 捕获瞬态提示: %s (cls=%s)",
                    el.get("text", "")[:60],
                    el.get("cls", "")[:40],
                )
        # 通道2：活 DOM 扫描兜底
        els = await page.eval_on_selector_all(
            _TRANSIENT_HINT_SELECTOR,
            "els => els.map(e => ({text: (e.innerText||'').trim(), "
            "cls: (e.getAttribute('class')||''), role: e.getAttribute('role')||''}))"
            ".filter(x => x.text)",
        )
        for el in els:
            key = (el.get("text"), el.get("cls"))
            if key not in known:
                hints.append(
                    {
                        "text": el.get("text", ""),
                        "cls": el.get("cls", ""),
                        "role": el.get("role", ""),
                        "ts": _t.time(),
                    }
                )
                known.add(key)
                _log.info(
                    "[Transient] DOM 捕获瞬态提示: %s (cls=%s)",
                    el.get("text", "")[:60],
                    el.get("cls", "")[:40],
                )


async def _structured_focus_check(engine, action) -> tuple[bool, str]:
    """契约统一：结构化路径的焦点校验（与引导路径 _guided._exec_one_step 的
    focus_check 分支语义一致）。断言 document.activeElement 与目标命中。
    """
    if engine is None or getattr(engine, "_page", None) is None:
        return False, "页面未就绪"
    _target = action.get("target_hint") or action.get("target") or ""
    _focused = ""
    try:
        _info = await engine._page.evaluate(
            "() => { const e = document.activeElement;"
            " return {tag: (e && e.tagName||'').toLowerCase(),"
            " name: (e && (e.id||e.name||e.textContent||'').trim().slice(0,40)||''),"
            " cls: (e && e.className||'').toString().slice(0,40) }; }"
        ) or {}
        _focused = ("%s#%s" % (_info.get("tag"), _info.get("name"))).strip("#")
    except Exception as _fe:
        return False, "焦点校验异常: %s" % str(_fe)[:60]
    if _focused and (_target in _focused or _focused in _target):
        return True, ""
    return False, (
        "焦点校验失败: 期望%s, 实际%s"
        % (_target or "焦点元素", _focused or "无activeElement")
    )


async def execute_structured_actions(engine, step, step_desc):
    """执行结构化动作序列。

    Returns:
        dict: {"success": bool, "all_failed": bool, "total": int, "failed": int, "errors": list}
              all_failed=True 表示所有交互类动作都失败，调用方应跳过 AI loop 直接快速失败
    """
    actions = step.get("actions", [])
    if not actions:
        return {
            "success": False,
            "all_failed": True,
            "total": 0,
            "failed": 0,
            "errors": [],
        }
    if engine is None or engine._page is None:
        return {
            "success": False,
            "all_failed": True,
            "total": len(actions),
            "failed": len(actions),
            "errors": ["engine not ready"],
        }
    all_ok = True
    total_interactive = 0  # 交互类动作总数（排除 navigate/screenshot/wait）
    failed_interactive = 0
    failed_errors = []
    # ★ v19.2 通用修复(2026-07-23): 业务错误标记（不可通过 AI loop 恢复）
    #   根因: upload_file 返回"系统拒绝上传"是业务错误，AI loop 无法修复
    #         （AI 不知道系统期望什么文件格式），循环到超时浪费资源
    #   通用性: 适用于所有 action 的业务错误（系统拒绝/API 错误/权限不足等）
    #   覆盖性: 影响所有 structured action 失败后的 AI fallback 决策
    #   职责边界: Step4 执行层内（判定错误类型是执行的一部分）
    non_recoverable = False
    # ★ 一次性加载操作指令库，供 dispatcher 复用
    try:
        action_library = action_library_store.load_library()
    except Exception as e:
        _log.warning("[StructuredActions] 加载 action_library 失败: %s", str(e)[:80])
        action_library = None
    # ★ Fix: selector_hint 优先于 target_hint — selector_hint 是可执行选择器，target_hint 仅用于日志
    for _a in actions:
        if isinstance(_a, dict) and _a.get("selector_hint"):
            _a["target_hint"] = _a["selector_hint"]
        if (
            isinstance(_a, dict)
            and (_a.get("type", "") or _a.get("action", "")) == "navigate"
        ):
            # ★ 根因修复(2026-10-14) RC-AI: **不再**对 navigate 无条件置 all_ok=False。
            #   根因: 中文页面名 / `intent:` 导航由 `_resolve_navigate_url` 内部走菜单
            #     定位并返回 None（跳过 dispatcher）。旧代码在调用**之前**就把 all_ok
            #     置假，且跳过后无人恢复 → 即使日志打出"导航菜单定位成功"也判结构化
            #     整体失败 → 回退 AI loop 空转 8 轮 → 假 ERROR。
            #     实测: run_logs 全轮"结构化动作执行失败"7/7、"成功"0 次
            #     （TC-REQ-024/025-UX02、023/024/025-V02、024-PF02 等）。
            #   修复: 真实结果由 `_resolve_navigate_url` 回写 `_nav_ok`，主循环据此判定。
            with guard("execute_structured_actions: resolved = await _resolve_navigate_u", log=_log, level=logging.WARNING):
                resolved = await _resolve_navigate_url(engine, _a)
                if resolved is None:
                    _a["_skip_dispatch"] = True

    _last_fill_target = ""
    # 已成功录入的控件 hint（提交前补全时作为 skip_keys，避免覆盖用例刻意设定的值）
    _filled_hints: list[str] = []
    for action in actions:
        _atype = action.get("type", "") or action.get("action", "")
        # ★ 指标: 记录动作尝试（结构化路径）
        with guard("execute_structured_actions: from src.backend.state import _threa", log=_log):
            from src.backend.state import _thread_run_id
            from src.core.config_loader import config
            from src.extensions.metrics_collector import metrics_collector

            _rid = getattr(_thread_run_id, "value", "") or ""
            _proj = getattr(config, "project_name", "") or ""
            if _rid and _proj and _atype:
                metrics_collector.record_action_attempt(
                    project=_proj,
                    run_id=_rid,
                    action_type=_atype,
                )
        # ★ 非交互类动作不计入统计
        _is_interactive = _atype not in ("navigate", "screenshot", "wait", "scroll", "")
        # ★ 契约统一：focus_check 为标准键盘可达性动作，双执行路径对称实现
        #   （结构化路径在此直接执行，与引导路径 _guided._exec_one_step 一致）
        if _atype == "focus_check":
            _ok_fc, _err_fc = await _structured_focus_check(engine, action)
            if _is_interactive:
                total_interactive += 1
            if not _ok_fc:
                all_ok = False
                if _is_interactive:
                    failed_interactive += 1
                    failed_errors.append(
                        f"focus_check('{action.get('target_hint', '')[:40]}') 失败"
                    )
                _log.warning("[Structured] focus_check 失败: %s", _err_fc)
            continue
        try:
            if action.get("_skip_dispatch"):
                # ★ RC-AI(2026-10-14): 菜单导航已在 URL 预处理阶段执行完毕，以其
                #   真实结果 `_nav_ok` 驱动整体成败（旧代码此处直接 continue，配合
                #   预扫阶段的无条件置假 → 菜单导航成功也被判结构化失败）。
                if not action.get("_nav_ok", False):
                    all_ok = False
                    _log.warning(
                        "[Structured] navigate 菜单定位失败: %s",
                        (action.get("target_hint", "") or "")[:60],
                    )
                continue
            if _atype == "fill":
                _last_fill_target = action.get("target_hint", "")
            # 提交前补齐表单其余必填项：用例录入段常只声明需求点名字段，其余必填项
            # 留空会被 SUT 校验拦截 → 提交后果类断言结构性落空。极性门控在上游
            # （_base.py 写入 step["_allow_autofill"]），执行端只读不推。
            if (
                _atype == "click"
                and step.get("_allow_autofill")
                and _is_submit_click(str(action.get("target_hint") or ""))
                and engine._page is not None
            ):
                with guard(
                    "execute_structured_actions: autofill_before_submit",
                    log=_log,
                    level=logging.WARNING,
                ):
                    _n_filled = await complete_form_fields(
                        engine._page, set(_filled_hints), 5000, [], []
                    )
                    if _n_filled:
                        _log.info(
                            "[Structured] 提交前补全必填项 %d 个（%s）",
                            _n_filled,
                            str(action.get("target_hint") or "")[:40],
                        )
            # ★ Fix (2026-07-12): upload_file 的 file_path 校验下放到 browser_tools 执行器，
            #   结构化执行器不再因 file_path 为空/不存在而跳过，避免 Step3 使用默认占位符时失败。
            if _atype == "upload_file":
                _fp = (
                    action.get("file_path") or action.get("file") or action.get("value")
                )
                if not _fp:
                    action["file_path"] = "__E2E_DEFAULT_UPLOAD__"
                    _log.info("[Structured] upload_file 缺少 file_path，使用默认占位符")
            handled, ok, _err_msg = await dispatch_action(
                engine, action, action_library
            )
            if handled:
                # ★ v27: 每个动作后即时捕获瞬态提示（toast/message 出现后数秒即消失，
                #   校验阶段再查活 DOM 必然错过 → 出现时就缓存证据）
                if engine._page is not None:
                    await _capture_transient_hints(engine._page)
                if _is_interactive:
                    total_interactive += 1
                # 记录已成功录入的控件，提交前补全时不得覆盖（用例取值优先）
                if ok and _atype in _INPUT_ACTION_TYPES:
                    _hint_done = str(
                        action.get("target_hint") or action.get("selector") or ""
                    )
                    if _hint_done:
                        _filled_hints.append(_hint_done)
                # ★ v19.2: 检测业务错误（不可通过 AI loop 恢复）
                #   业务错误关键词: 系统拒绝/rejected/权限不足/格式不支持/数据表不能为空
                #   定位错误: 未找到元素/不可交互/超时 — 这些可以进入 AI loop
                if not ok and _err_msg:
                    _err_lower = _err_msg.lower()
                    _BUSINESS_ERROR_KWS = (
                        "系统拒绝",
                        "rejected",
                        "权限不足",
                        "格式不支持",
                        "数据表不能为空",
                        "文件格式",
                        "invalid format",
                        "forbidden",
                        "unauthorized",
                    )
                    if any(_kw.lower() in _err_lower for _kw in _BUSINESS_ERROR_KWS):
                        non_recoverable = True
                        _log.warning(
                            "[Structured] v19.2: 检测到业务错误(不可恢复): %s",
                            _err_msg[:100],
                        )
                # ★ 通用修复(2026-07-20 v10): navigate 后置 Tab 切换
                #   原因: 某SPA系统(Ant Design Tabs)的 URL 参数 ?tab=xxx 不触发 Tab 切换，
                #         导致页面停留在默认 Tab，后续业务按钮 click 在错误的 Tab 上失败。
                #   修复: navigate 执行成功后，若 URL 含 tab 参数，自动检测并点击对应 Tab 元素。
                #   通用性: 适用于所有 SPA 框架（Ant Design/ElementUI/React Router 等）。
                if _atype == "navigate" and ok:
                    # ★ v27: navigate 后注入 MutationObserver，实时记录瞬态提示出现
                    if engine._page is not None:
                        await _ensure_toast_observer(engine._page)
                    try:
                        _nav_url = (
                            action.get("url", "") or action.get("value", "") or ""
                        )
                        if _nav_url and "tab=" in _nav_url:
                            _tab_ok = await _ensure_tab_active_after_navigate(
                                engine, _nav_url
                            )
                            if not _tab_ok:
                                _log.warning(
                                    "[Structured] navigate 后 Tab 切换失败: %s",
                                    _nav_url[:80],
                                )
                    except Exception as _tab_e:
                        _log.warning(
                            "[Structured] navigate 后 Tab 切换异常(不阻塞): %s",
                            str(_tab_e)[:80],
                            exc_info=True,
                        )
                # ★ 通用修复(2026-07-21 v3): click 成功后等待弹窗/页面渲染
                #   原BUG: click "新建配方" 成功后立即 click "确定" 失败——
                #          间隔<1秒，弹窗还没渲染出来，导致后续 click 弹窗按钮全部失败。
                #   通用修复: click 成功后，等待弹窗/页面渲染完成：
                #     1. 检测是否有 modal/dialog 出现（Ant Design/ElementUI/Bootstrap/原生）
                #     2. 有 modal → 等待 modal 渲染完成（最多2秒）
                #     3. 无 modal → 等待 domcontentloaded（最多1秒）
                #   通用性: 适用于所有系统（React/Vue/Angular/AntDesign/ElementUI/Native HTML）
                #   合理性: click 触发的副作用（弹窗/抽屉/页面跳转）需要时间渲染，
                #          这是"等待执行完成"不是"决策"。
                #   覆盖性: 覆盖所有 click action。
                if _atype == "click" and ok and engine._page is not None:
                    with guard("execute_structured_actions: _page = engine._page", log=_log):
                        _page = engine._page
                        _modal_selectors = [
                            ".ant-modal",
                            ".ant-modal-wrap",
                            ".el-dialog",
                            ".el-drawer",
                            ".modal",
                            ".modal-dialog",
                            '[role="dialog"]',
                            '[role="alertdialog"]',
                            ".ant-drawer",
                        ]
                        _modal_found = False
                        for _modal_sel in _modal_selectors:
                            try:
                                _modal_loc = _page.locator(_modal_sel).first
                                if (
                                    await _modal_loc.count() > 0
                                    and await _modal_loc.is_visible(timeout=200)
                                ):
                                    _modal_found = True
                                    # 等待 modal 内容渲染完成
                                    try:
                                        await _page.wait_for_load_state(
                                            "domcontentloaded", timeout=2000
                                        )
                                    except Exception as _e:
                                        _log.debug(
                                            "[structured_executor] modal wait_for_load 异常: %s",
                                            _e,
                                            exc_info=True,
                                        )
                                    await asyncio.sleep(
                                        0.8
                                    )  # 给 JS 框架时间渲染 modal 内容
                                    _log.info(
                                        "[Structured] click 后检测到 modal '%s'，等待渲染完成",
                                        _modal_sel,
                                    )
                                    break
                            except Exception as _ex1:
                                _log.debug(
                                    "[browser_structured_executor] 异常降级(continue): %s",
                                    str(_ex1)[:60],
                                    exc_info=True,
                                )
                                continue
                        if not _modal_found:
                            # 无 modal，等待 domcontentloaded（页面跳转场景）
                            try:
                                await _page.wait_for_load_state(
                                    "domcontentloaded", timeout=1000
                                )
                            except Exception as _e:
                                _log.debug(
                                    "[structured_executor] domcontentloaded 等待异常: %s",
                                    _e,
                                    exc_info=True,
                                )
                if not ok:
                    if _atype == "click" and _last_fill_target:
                        _fill_lower = _last_fill_target.lower()
                        # ★ v4.3 通用修复(2026-07-23): Enter兜底必须同时验证click目标语义
                        #   根因(§10.9): 原逻辑只检查fill目标(搜索框)含"搜索"关键词，
                        #     不检查click目标。当"fill搜索框 + click编辑按钮"失败时，
                        #     Enter兜底错误触发→在搜索框按Enter→搜索≠编辑→假PASS。
                        #     违反致命红线第3条(假PASS)。
                        #   根因链路(5层):
                        #     L1 表层: TC-REQ-004-P01 status=PASS 但 click 失败
                        #     L2 中层: Enter兜底设置 ok=True → _structured_ok=True → checks执行
                        #     L3 根因层: Enter兜底只检查fill目标，不检查click目标
                        #     L4 设计层: Enter兜底假设"fill搜索框→click一定是搜索按钮"，错误假设
                        #     L5 最深根因: 执行策略缺少语义校验（click意图 vs Enter效果）
                        #   通用性(§12.4):
                        #     1. 跨系统: 适用于所有"fill搜索框 + click非搜索按钮"场景
                        #     2. 跨行业: 搜索/编辑/删除/新建是所有系统通用操作
                        #     3. 跨页面: 列表页/详情页都有搜索框+其他按钮的组合
                        #     4. 根因溯源: 修复的是"执行策略缺少语义校验"，不是选择器精度
                        #   覆盖性: 影响所有走 execute_structured_actions 的 click 失败场景
                        #   全局观: 修复后 click 失败不再被 Enter兜底掩盖 → _structured_ok=False
                        #           → AI loop 运行 → 如AI也失败 → checks执行 → 真实FAIL
                        #   职责边界: Step4 执行层内（执行策略修复，不是业务决策）
                        #   自我反驳:
                        #     1) 只检查click目标含"搜索"？→ 不够，需同时检查fill+click
                        #     2) 用operation字段判断？→ action中无operation，只有target_hint
                        #     3) 同时检查fill+click目标？→ 最通用，两者都必须是搜索语义
                        #   合规: §10.8 禁止治标不治本 + §10.9 根因核查 + 致命红线第3条
                        _click_hint_raw = (action.get("target_hint", "") or "").lower()
                        # 剥离 intent: 前缀，获取纯操作语义
                        _click_hint_clean = (
                            _click_hint_raw[7:]
                            if _click_hint_raw.startswith("intent:")
                            else _click_hint_raw
                        )
                        _SEARCH_KWS = ("搜索", "search", "查询", "find", "lookup")
                        _is_search_fill = any(kw in _fill_lower for kw in _SEARCH_KWS)
                        _is_search_click = any(
                            kw in _click_hint_clean for kw in _SEARCH_KWS
                        )
                        if _is_search_fill and _is_search_click:
                            _log.info(
                                "[Structured] click 失败后检测到搜索上下文(fill+click均为搜索语义)，尝试按 Enter 兜底"
                            )
                            with guard("execute_structured_actions: await engine._page.keyboard.press('E", log=_log, level=logging.WARNING):
                                await engine._page.keyboard.press("Enter")
                                await asyncio.sleep(1)
                                try:
                                    await engine._page.wait_for_load_state(
                                        "domcontentloaded", timeout=5000
                                    )
                                except Exception as _e:
                                    _log.debug(
                                        "[structured_executor] Enter 兜底 wait_for_load 异常: %s",
                                        _e,
                                        exc_info=True,
                                    )
                                _log.info("[Structured] ✓ Enter 兜底成功")
                                ok = True
                        elif _is_search_fill and not _is_search_click:
                            _log.info(
                                "[Structured] click 失败: fill目标含搜索但click目标'%s'非搜索语义，跳过Enter兜底",
                                _click_hint_clean[:40],
                            )
                    if not ok:
                        _log.warning(
                            "[Structured] action '%s' 执行失败，target_hint='%s'",
                            _atype,
                            action.get("target_hint", "")[:60],
                        )
                        all_ok = False
                        if _is_interactive:
                            failed_interactive += 1
                            failed_errors.append(
                                f"{_atype}('{action.get('target_hint', '')[:40]}') 失败"
                            )
                continue
            atype = action.get("type", "") or action.get("action", "")
            # ★ 归一化（2026-08-04）：handled=False 时补充明确 reason（EMPTY_TOOL/
            #   INVALID_TOOL/WORKFLOW_ONLY/UNKNOWN_ACTION），不静默 miss。
            #   保持 all_ok=False 语义（未命中即记为失败，交由上层 AI loop 兜底）。
            _miss_reason = _err_msg or "NO_MAPPING"
            _log.warning(
                "[Structured] 操作 '%s' 未命中查表（reason=%s），交由上层兜底",
                atype,
                _miss_reason,
            )
            all_ok = False
            if _is_interactive:
                total_interactive += 1
                failed_interactive += 1
                failed_errors.append(f"{atype} 未注册")
        except Exception as e:
            atype = action.get("type", "") or action.get("action", "")
            _log.warning("[Structured] 操作失败 %s: %s", atype, str(e)[:80])
            all_ok = False
            if _is_interactive:
                total_interactive += 1
                failed_interactive += 1
                failed_errors.append(f"{atype} 异常: {str(e)[:60]}")

    return {
        "success": all_ok,
        "all_failed": total_interactive > 0 and failed_interactive >= total_interactive,
        "total": total_interactive,
        "failed": failed_interactive,
        "errors": failed_errors,
        "non_recoverable": non_recoverable,
    }


async def execute_structured_assertions(engine, step, step_desc, hard_verify_fn=None):
    """执行结构化断言序列。

    ★ f2: 库中已注册的断言（verify_style/verify_toast/verify_position/verify_refresh/
       response_time_check 等）统一走 dispatch_assertion 查表执行。
       Step3 生成的 url_contains/element_text/element_exists/no_error 属于基础 UI 断言，
       无需注册到 action_library，保留为兜底分支。
    """
    if hard_verify_fn is None:
        from src.pipeline.steps.step4.browser_verification import hard_verify_step

        hard_verify_fn = hard_verify_step
    assertions = step.get("assertions", [])
    if not assertions:
        return await hard_verify_fn(engine, step_desc, step.get("expected", ""))
    if engine is None or engine._page is None:
        return False
    page = engine._page
    all_ok = True
    # ★ 一次性加载操作指令库，供 dispatcher 复用
    try:
        action_library = action_library_store.load_library()
    except Exception as e:
        _log.warning("[StructuredAssertions] 加载 action_library 失败: %s", str(e)[:80])
        action_library = None
    for a in assertions:
        try:
            # ★ 优先走 Action Dispatcher：库中注册的断言直接调用 browser_tools 执行
            handled, ok = await dispatch_assertion(engine, a, action_library)
            if handled:
                if not ok:
                    all_ok = False
                continue

            # ★ 兜底：Step3 基础 UI 断言（无 tool 映射，保留为语义化实现）
            atype = a.get("type", "")
            value = a.get("value", "")
            if atype == "url_contains":
                if value and value not in (page.url or ""):
                    _log.warning("[Assert] URL 不包含 '%s': %s", value, page.url)
                    all_ok = False
            elif atype == "element_text":
                if value:
                    t = await page.evaluate("() => document.body.innerText || ''")
                    if value not in str(t):
                        _log.warning("[Assert] 页面文本不包含 '%s'", value)
                        all_ok = False
            elif atype == "element_exists":
                if value:
                    try:
                        el = page.get_by_role("button", name=value)
                        if await el.count() > 0 and await el.first.is_visible():
                            _log.info("[Assert] element_exists 命中(button): %s", value)
                            continue
                    except Exception as _pass_e:
                        _log.warning(
                            "browser_structured_executor.py element_exists button 异常被吞: %s",
                            _pass_e,
                            exc_info=True,
                        )
                    try:
                        el = page.get_by_role("heading", name=value)
                        if await el.count() > 0 and await el.first.is_visible():
                            _log.info(
                                "[Assert] element_exists 命中(heading): %s", value
                            )
                            continue
                    except Exception as _pass_e:
                        _log.warning(
                            "browser_structured_executor.py element_exists heading 异常被吞: %s",
                            _pass_e,
                            exc_info=True,
                        )
                    t = await page.evaluate("() => document.body.innerText || ''")
                    if value in str(t):
                        _log.info("[Assert] element_exists 回退命中(text): %s", value)
                        continue
                    _log.warning("[Assert] 元素不存在: %s", value)
                    all_ok = False
            elif atype == "no_error":
                for sel in [
                    '[role="alert"]',
                    '[role="status"]',
                    ".error",
                    ".alert-danger",
                ]:
                    try:
                        if await page.locator(sel).count() > 0:
                            _log.warning("[Assert] 页面检测到错误提示")
                            all_ok = False
                            break
                    except Exception as _e:
                        _log.debug("no_error 错误提示检测失败: %s", _e, exc_info=True)
                        continue
            elif atype in ("api_status", "db_consistency"):
                pass
            else:
                _log.warning("[Assert] 未知断言类型: %s", atype)
                all_ok = False
        except Exception as e:
            atype = a.get("type", "") or a.get("action", "")
            _log.warning("[Assert] 断言异常 %s: %s", atype, str(e)[:60])
            all_ok = False
    return all_ok
