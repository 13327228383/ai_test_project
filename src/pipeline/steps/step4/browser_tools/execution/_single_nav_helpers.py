# -*- coding: utf-8 -*-
"""
导航与基础操作辅助模块（从 _single.py 拆分）
============================================
★ 架构债清理(2026-07-25): 基础拖拽/填充/选择 + 浏览器导航处理器
  拆分至本文件（§4.3 单文件≤800行）。

职责边界：
  - _do_drag: 拖拽操作辅助
  - _fill_by_hint: 根据 hint 定位元素后 fill（含前置可交互验证）
  - _select_by_hint: 根据 hint 定位元素后 select_option（含前置可交互验证）
  - _handle_browser_navigate: 独立 browser_navigate 处理器（含错误页/SPA/菜单回退）

通用性：零业务硬编码，纯基于通用 UI 框架选择器 + ARIA 语义。
"""

import asyncio
from src.core.guarded import guard
import logging

from ._result import ToolExecutionResult
from ..locator import _locate_with_fallback, pre_verify_interactive_element
from src.pipeline.steps.step4.action_hooks import run_action_with_hooks
from src.core.page_ready import wait_page_ready

# 依赖：菜单导航模块（同目录）
from ._single_menu import _fallback_menu_navigate

# 依赖：页面状态检测模块（同目录）
from ._single_nav_state import (
    _check_content_interactive,
    _check_content_structural,
    _check_error_page,
    _try_relogin_to_target,
    page_is_denial_response,
)

_log = logging.getLogger(__name__)


async def _do_drag(page, src, tgt):
    """辅助：执行拖拽操作。"""
    await src.hover(timeout=3000)
    await page.mouse.down()
    await tgt.hover(timeout=3000)
    await page.mouse.up()


async def _fill_by_hint(page, hint: str, text: str, selector: str = ""):
    """辅助：根据 hint 定位元素后 fill，支持自愈后的新 hint 和预解析选择器。"""
    locator = await _locate_with_fallback(page, hint, kind="input", selector=selector)
    if not locator:
        raise ValueError(f"未找到元素: {hint}")
    # ★ 前置验证：元素可交互（存在 + 可见 + 未禁用）
    verify = await pre_verify_interactive_element(locator)
    if not verify.passed:
        raise ValueError(f"元素不可交互: {hint} — {verify.message}")
    await locator.fill(text, timeout=5000)


async def _select_by_hint(page, hint: str, option_text: str, selector: str = ""):
    """辅助：根据 hint 定位元素后 select_option，支持自愈后的新 hint 和预解析选择器。

    ★ 通用修复(2026-09-27)：Step3 用**语义哨兵值**表达"取下拉首项/有效项"
      （"第一项"/"第一项(默认)"/"随机一项"），而这类文本并非真实 option 文本
      → `select_option(label=...)` 必抛错 → 结构化执行失败 → 降级 AI 探索。
      修复：哨兵值回落为「按 index 选第一个非空选项」，非哨兵值仍按 label 精确选择。
      通用性：哨兵值属生成/执行契约约定（跨系统/行业一致），零业务硬编码。
    """
    locator = await _locate_with_fallback(page, hint, kind="input", selector=selector)
    if not locator:
        raise ValueError(f"未找到元素: {hint}")
    # ★ 前置验证：元素可交互（存在 + 可见 + 未禁用）
    verify = await pre_verify_interactive_element(locator)
    if not verify.passed:
        raise ValueError(f"元素不可交互: {hint} — {verify.message}")

    # 语义哨兵值 → 按 index 选首个非空选项（真实 option 文本未知，label 选择必失败）
    _sentinel = str(option_text or "").strip()
    if _sentinel in ("第一项", "第一项(默认)", "随机一项", "有效选项", "任意一项"):
        try:
            _opts = await locator.locator("option").all()
        except Exception:  # noqa: BLE001
            _opts = []
        for _i, _o in enumerate(_opts):
            try:
                _val = (await _o.get_attribute("value")) or ""
                _txt = (await _o.inner_text()) or ""
                # 跳过占位项（value 为空 或 文本含"请选择/全部"），取首个真实可选项
                if _val and "请选择" not in _txt and "全部" not in _txt:
                    await locator.select_option(index=_i, timeout=5000)
                    return
            except Exception:  # noqa: BLE001
                continue
        # 无非空可选项（仅占位项）→ 按下标 1 兜底；仍失败则如实抛错
        await locator.select_option(index=1 if len(_opts) > 1 else 0, timeout=5000)
        return

    await locator.select_option(label=option_text, timeout=5000)


async def _handle_browser_navigate(engine, args, hint):
    """独立的 browser_navigate 处理器（>50行，提取为独立函数）。"""
    try:
        url = args.get("url", "")

        # ★ 通用修复 (2026-07-18 v2): URL 为空时，直接走菜单导航
        #   当 Step3 AI 生成的 URL 无效（中文文本等）被 dispatcher 过滤后，url 为空。
        #   此时直接使用 target_hint 走菜单导航，避免无效的 goto("") 调用。
        if not url or not url.strip():
            _log.info("[Navigate] URL 为空，尝试菜单导航: hint=%s", hint[:80])
            menu_ok = await _fallback_menu_navigate(engine, url, hint)
            if menu_ok:
                return ToolExecutionResult(True, "菜单导航成功(hint: %s)" % hint[:40])
            else:
                return ToolExecutionResult(False, "URL为空且菜单导航失败")

        nav_result = await run_action_with_hooks(
            engine,
            "navigate",
            url,
            do_action=lambda a, u=url: engine.goto(a.get("url", u)),
            action_extra={"url": url},
        )
        ok = nav_result.get("success", False)  # run_action_with_hooks 始终返回 dict

        # ★ Fix (2026-07-16): 导航后检测错误页面，回退到菜单导航
        if ok:
            page = getattr(engine, "_page", None)
            if page:
                # 就绪条件等待（唯一实现）。goto 已内部条件等待，此处只为错误页判定
                # 取到稳定快照；原为固定 sleep(2)（每次导航白烧 2s）。
                await wait_page_ready(page)
                is_error_page = await _check_error_page(page)
                if is_error_page:
                    # ★ 通用修复 (2026-07-17): 先尝试重新登录目标系统（会话过期场景）
                    _log.warning("[Navigate] 检测到错误页面，尝试重新登录: %s", url)
                    relogin_ok = await _try_relogin_to_target(engine, page)
                    if relogin_ok:
                        _log.info("[Navigate] 重新登录成功，导航到目标系统首页")
                        # 重试导航到目标系统首页（而非失败URL）
                        from src.core.config_loader import config

                        base_url = config.target_url or url
                        await engine.goto(base_url)
                        await wait_page_ready(page)
                        is_error_page = await _check_error_page(page)
                        if not is_error_page:
                            _log.info("[Navigate] 重新登录后导航成功(首页)")
                            return ToolExecutionResult(
                                True, "重登后导航到首页: " + base_url
                            )

                    if is_error_page:
                        _log.warning(
                            "[Navigate] 仍为错误页面，尝试菜单导航回退: %s", url
                        )
                        menu_ok = await _fallback_menu_navigate(engine, url, hint)
                        if menu_ok:
                            _log.info("[Navigate] 菜单导航回退成功")
                            return ToolExecutionResult(True, "菜单导航到: " + url)
                        else:
                            _log.warning("[Navigate] 菜单导航回退也失败")
                            return ToolExecutionResult(
                                False, "页面不存在且菜单导航失败: " + url
                            )

        # ★ Fix51 (2026-07-09): SPA 页面 navigate 后自动点击 tab 触发渲染
        # ★ Fix56 : 修复 3 个缺陷：
        #   1. URL 无 tab 参数时也能触发（从 action target_hint 推断 tab 文本）
        #   2. input_cnt==0 过严（改为检测内容区交互元素，而非全页面）
        #   3. 增加等待和重试逻辑
        if ok:
            with guard("_handle_browser_navigate: from urllib.parse import urlparse, p", log=_log):
                from urllib.parse import urlparse, parse_qs, unquote

                parsed = urlparse(url)
                qs = parse_qs(parsed.query)
                tab_val = (qs.get("tab", [""])[0] if "tab" in qs else "").strip()
                tab_val = unquote(tab_val) if tab_val else ""

                page = getattr(engine, "_page", None)
                if page:
                    # 等「可交互」条件而非固定 3s —— 热页 ~100ms 返回（全量 run 省 ≥30min）。
                    await wait_page_ready(page)

                    content_total = await _check_content_interactive(page)

                    # ★ Fix: 内容区空时先等 SPA 渲染（最多再等 10s），再决定是否点 tab
                    if content_total < 2:
                        _log.info(
                            "[Fix] 内容区初始空(%d)，额外等待SPA渲染...", content_total
                        )
                        # 200ms 粒度（原 1s）→ 渲染完成即刻放行，不再整秒空转。
                        for _w in range(50):
                            await asyncio.sleep(0.2)
                            content_total = await _check_content_interactive(page)
                            if content_total >= 2:
                                _log.info(
                                    "[Fix] SPA 渲染完成，内容区有 %d 个元素",
                                    content_total,
                                )
                                break

                    if content_total < 2:
                        # 内容区交互元素不足 → 需要 tab 点击触发渲染
                        # 推断 tab 文本：URL tab 参数 > action target_hint > 需求关键词
                        tab_text = tab_val
                        if not tab_text:
                            # 从 action 的 target_hint 或描述推断
                            # 常见 tab 文本：辅材配方、叶组配方、产品配方 等
                            # 通用逻辑：查找页面上的 tab/label 元素
                            tab_elements = await page.locator(
                                ".el-tabs__item, .ant-tabs-tab, "
                                ".tab-pane, [role='tab'], "
                                "label.tab, .nav-tabs li"
                            ).all_text_contents()
                            if tab_elements:
                                _log.info(
                                    "[Fix56] 页面有 %d 个 tab 元素: %s",
                                    len(tab_elements),
                                    tab_elements[:5],
                                )
                                # 找第一个可点击的 tab（跳过已激活的）
                                for te in tab_elements[:5]:
                                    te = te.strip()
                                    if te and len(te) < 20:
                                        tab_text = te
                                        break

                        if tab_text:
                            _log.info(
                                "[Fix56] SPA 内容区空(%d 元素)，点击 tab='%s'",
                                content_total,
                                tab_text[:30],
                            )
                            tab_loc = page.get_by_text(tab_text, exact=False).first
                            with guard("_handle_browser_navigate: if await tab_loc.count() > 0: await ", log=_log):
                                if await tab_loc.count() > 0:
                                    await tab_loc.click(timeout=5000)
                                    # ★ Fix: tab 点击后等待时间从 2s → 5s，并轮询检测
                                    for _w in range(5):
                                        await asyncio.sleep(1)
                                        _cnt = await _check_content_interactive(page)
                                        if _cnt >= 2:
                                            _log.info(
                                                "[Fix] tab 渲染完成，内容区有 %d 个元素",
                                                _cnt,
                                            )
                                            break
                                    else:
                                        _log.warning("[Fix] tab 点击后 5s 内容区仍空")

        # ★ 通用修复 (2026-07-17): URL导航后内容区仍空，回退到菜单导航
        if ok:
            page = getattr(engine, "_page", None)
            if page:
                content_total = await _check_content_interactive(page)
                # ★ 内容区交互元素少 ≠ 页面空白（单一判据，与 _check_content_structural
                #   同源）：只读列表/详情页交互元素天然稀少，不得据此推断导航失败
                #   —— 否则结构化动作失败 → 回落 AI 兜底（运行期零 AI）→ 用例 SKIP。
                #   仅当内容区**既无交互元素也无结构性内容**才判"未抵达"。
                _content_structural = await _check_content_structural(page)
                if content_total < 2 and _content_structural == 0:
                    # ★ 期望被拒绝的用例：拒绝页（403/无权限）本就是目标状态，
                    #   其内容区天然只有极少交互元素 —— 不能用"内容区空"推断导航失败。
                    #   「是否期望被拒绝」是 Step4 意图入口写入的单源事实
                    #   （src.core.case_context，生产者唯一 case_expects_denial），
                    #   此处只读不推；拒绝/放行由断言层裁决，导航层只答"是否抵达"。
                    _expects_denial = False
                    with guard(
                        "_handle_browser_navigate: current_case_expects_denial()", log=_log
                    ):
                        from src.core.case_context import current_case_expects_denial

                        _expects_denial = current_case_expects_denial()
                    if _expects_denial:
                        _body_text = ""
                        try:
                            _body_text = await page.locator("body").inner_text(
                                timeout=3000
                            )
                        except Exception as _e:
                            _log.debug("[Navigate] 读取 body 失败: %s", str(_e)[:60])
                        if page_is_denial_response(_body_text):
                            _log.info(
                                "[Navigate] 内容区空但已抵达期望的拒绝页 → 导航成功: %s",
                                url[:80],
                            )
                            return ToolExecutionResult(
                                True, "已抵达期望的拒绝页: " + url
                            )
                    _log.warning(
                        "[Navigate] URL导航后内容区空(%d元素)，尝试菜单导航: %s",
                        content_total,
                        url,
                    )
                    menu_ok = await _fallback_menu_navigate(engine, url, hint)
                    if menu_ok:
                        _log.info("[Navigate] 菜单导航回退成功")
                        return ToolExecutionResult(True, "菜单导航到: " + url)
                    else:
                        _log.warning("[Navigate] 菜单导航回退也失败")
                        return ToolExecutionResult(
                            False, "页面空白且菜单导航失败: " + url
                        )

        return ToolExecutionResult(ok, "导航到: " + url)
    except Exception as e:
        return ToolExecutionResult(False, "导航失败: " + str(e))
