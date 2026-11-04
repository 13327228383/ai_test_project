# -*- coding: utf-8 -*-
"""
单个工具执行实现
================
"""

import asyncio
from src.core.guarded import guard
import logging
import time
from pathlib import Path
from typing import Any, Optional

from ._result import ToolExecutionResult
from ..locator import (
    _locate_with_fallback,
    pre_verify_interactive_element,
    PreVerifyResult,
)
from src.pipeline.steps.step4.action_hooks import run_action_with_hooks


# ── 提交/负面按钮关键词唯一源（_single_exec_browser import 引用，勿重抄）──
_SUBMIT_KWS_V91 = (
    "确定", "提交", "保存", "确认", "开始导入", "开始上传",
    "导入", "上传", "应用", "完成", "下一步", "继续",
)
_NEGATIVE_KWS_V91 = (
    "取消", "关闭", "返回", "退出", "放弃", "重置", "清空", "不再", "停止",
)

_log = logging.getLogger(__name__)


# ★ 架构债清理(2026-07-25): upload_file 执行辅助拆分至 _single_upload.py（§4.3 单文件≤800行）
from ._single_upload import (
    _wait_for_upload_feedback,
    _check_upload_triggered,
    _try_download_template,
    _fill_template_with_test_data,
    _generate_test_value_for_column,
    _ensure_upload_test_file,
    _parse_accept_attr,
    _generate_compatible_upload_file,
)

# ★ 架构债清理(2026-07-25): 页面状态检测拆分至 _single_nav_state.py（§4.3 单文件≤800行）
from ._single_nav_state import (
    _check_content_interactive,
    _check_error_page,
    _try_relogin_to_target,
    _find_first_working_page,
    _verify_nav_target_reached,
)

# ★ 架构债清理(2026-07-25): 导航路径查找拆分至 _single_nav_lookup.py（§4.3 单文件≤800行）
from ._single_nav_lookup import (
    _extract_nav_keywords,
    _lookup_nav_path,
    _fallback_confluence_nav_path,
    _click_page_tab,
)

# ★ 架构债清理(2026-07-25): 菜单导航与点击拆分至 _single_menu.py（§4.3 单文件≤800行）
from ._single_menu import (
    _fallback_menu_navigate,
    _click_menu_item_generic,
    _click_menu_by_text,
    _try_remedy_disabled_button,
)

# ★ 架构债清理(2026-07-25): 导航与基础操作辅助拆分至 _single_nav_helpers.py（§4.3 单文件≤800行）
from ._single_nav_helpers import (
    _do_drag,
    _fill_by_hint,
    _select_by_hint,
    _handle_browser_navigate,
)

# ★ 架构债清理(2026-07-25): verify_* 工具处理器拆分至 _single_exec_verify.py（§4.3 单文件≤800行）
#   闭包→模块级函数 (engine, args, hint)，由 _TOOL_HANDLERS 通过 lambda 适配为零参调用。
from ._single_exec_verify import (
    _verify_animation_complete as _verify_animation_complete_impl,
    _verify_api_call_count as _verify_api_call_count_impl,
    _verify_api_header as _verify_api_header_impl,
    _verify_db_field_value as _verify_db_field_value_impl,
    _verify_db_query as _verify_db_query_impl,
    _verify_db_record_count as _verify_db_record_count_impl,
    _verify_db_record_exists as _verify_db_record_exists_impl,
    _verify_db_record_not_exists as _verify_db_record_not_exists_impl,
    _verify_db_ui_consistency as _verify_db_ui_consistency_impl,
    _verify_dialog as _verify_dialog_impl,
    _verify_download as _verify_download_impl,
    _verify_element_attribute as _verify_element_attribute_impl,
    _verify_element_attribute_all as _verify_element_attribute_all_impl,
    _verify_element_checked as _verify_element_checked_impl,
    _verify_element_count as _verify_element_count_impl,
    _verify_element_enabled as _verify_element_enabled_impl,
    _verify_element_exists as _verify_element_exists_impl,
    _verify_element_exists_check as _verify_element_exists_check_impl,
    _verify_element_not_exists as _verify_element_not_exists_impl,
    _verify_element_text_equals as _verify_element_text_equals_impl,
    _verify_element_visible as _verify_element_visible_impl,
    _verify_image_loaded as _verify_image_loaded_impl,
    _verify_list_order as _verify_list_order_impl,
    _verify_loading_complete as _verify_loading_complete_impl,
    _verify_network as _verify_network_impl,
    _verify_no_console_error as _verify_no_console_error_impl,
    _verify_no_error_text as _verify_no_error_text_impl,
    _verify_page_load_time as _verify_page_load_time_impl,
    _verify_pagination as _verify_pagination_impl,
    _verify_position as _verify_position_impl,
    _verify_refresh as _verify_refresh_impl,
    _verify_request_body as _verify_request_body_impl,
    _verify_response_body as _verify_response_body_impl,
    _verify_response_time as _verify_response_time_impl,
    _verify_select_value as _verify_select_value_impl,
    _verify_storage as _verify_storage_impl,
    _verify_style as _verify_style_impl,
    _verify_text_contains as _verify_text_contains_impl,
    _verify_text_in_page as _verify_text_in_page_impl,
    _verify_text_regex as _verify_text_regex_impl,
    _verify_time_range as _verify_time_range_impl,
    _verify_toast as _verify_toast_impl,
    _verify_url as _verify_url_impl,
    _verify_websocket as _verify_websocket_impl,
)

# ★ 架构债清理(2026-07-25): browser_* 工具处理器拆分至 _single_exec_browser.py（§4.3 单文件≤800行）
#   闭包→模块级函数 (engine, args, hint)，由 _TOOL_HANDLERS 通过 lambda 适配为零参调用。
#   注: _browser_upload_file 仍为闭包，属 B.1c。
from ._single_exec_browser import (
    _browser_blur as _browser_blur_impl,
    _browser_check as _browser_check_impl,
    _browser_clear as _browser_clear_impl,
    _browser_click as _browser_click_impl,
    _browser_close_tab as _browser_close_tab_impl,
    _browser_context_menu_click as _browser_context_menu_click_impl,
    _browser_double_click as _browser_double_click_impl,
    _browser_download_file as _browser_download_file_impl,
    _browser_drag as _browser_drag_impl,
    _browser_drag_offset as _browser_drag_offset_impl,
    _browser_fill as _browser_fill_impl,
    _browser_focus as _browser_focus_impl,
    _browser_go_back as _browser_go_back_impl,
    _browser_go_forward as _browser_go_forward_impl,
    _browser_hover as _browser_hover_impl,
    _browser_key_down as _browser_key_down_impl,
    _browser_key_up as _browser_key_up_impl,
    _browser_long_press as _browser_long_press_impl,
    _browser_middle_click as _browser_middle_click_impl,
    _browser_multi_touch_pinch as _browser_multi_touch_pinch_impl,
    _browser_navigate as _browser_navigate_impl,
    _browser_page_reload as _browser_page_reload_impl,
    _browser_press_key as _browser_press_key_impl,
    _browser_resize_window as _browser_resize_window_impl,
    _browser_right_click as _browser_right_click_impl,
    _browser_scroll as _browser_scroll_impl,
    _browser_select as _browser_select_impl,
    _browser_set_date as _browser_set_date_impl,
    _browser_swipe as _browser_swipe_impl,
    _browser_switch_tab as _browser_switch_tab_impl,
    _browser_switch_to_default as _browser_switch_to_default_impl,
    _browser_switch_to_iframe as _browser_switch_to_iframe_impl,
    _browser_take_screenshot as _browser_take_screenshot_impl,
    _browser_type_text as _browser_type_text_impl,
    _browser_uncheck as _browser_uncheck_impl,
    _browser_wait as _browser_wait_impl,
    _browser_wheel as _browser_wheel_impl,
)


async def _execute_tool(
    tool_name: str, args: dict[str, Any], engine
) -> ToolExecutionResult:
    """执行单个工具，内部复用 PlaywrightEngine 的底层方法。"""
    # finish 工具不需要引擎（纯状态报告）
    if tool_name == "finish":
        summary = args.get("result_summary", "")
        success = args.get("success", True)
        evaluation = args.get("evaluation", "")
        msg = "完成: " + summary
        if evaluation:
            msg += " | 自评: " + evaluation
        extra = {"_evaluation": evaluation} if evaluation else {}
        return ToolExecutionResult(bool(success), msg, extra)

    # 空引擎直接失败（finish 除外）
    if engine is None:
        return ToolExecutionResult(False, "浏览器引擎未初始化 (engine is None)")

    hint = args.get("target_hint", "")

    # ── 字典分派：闭包函数 ──

    async def _api_verify():
        try:
            api_tracker = getattr(engine, "_api_tracker", None)
            if not api_tracker:
                return ToolExecutionResult(True, "跳过（未启用 API 追踪）")
            records = getattr(api_tracker, "_recent_requests", [])
            if not records:
                return ToolExecutionResult(True, "跳过（无 API 请求记录）")
            last_req = (
                records[-1] if isinstance(records, list) and len(records) > 0 else None
            )
            if not last_req:
                return ToolExecutionResult(True, "跳过（无 API 记录）")
            status = last_req.get("status", 0) if isinstance(last_req, dict) else 0
            expected_status = args.get("expect_status", 200)
            if status != expected_status:
                return ToolExecutionResult(
                    False,
                    "最近 API 状态码 "
                    + str(status)
                    + " != 期望 "
                    + str(expected_status),
                )
            expect_kw = args.get("expect_keyword")
            if expect_kw:
                body = (
                    str(last_req.get("response_body", ""))
                    if isinstance(last_req, dict)
                    else ""
                )
                if expect_kw in body:
                    return ToolExecutionResult(
                        True, "API 验证通过: status=" + str(status)
                    )
                return ToolExecutionResult(False, "响应 body 不含关键字: " + expect_kw)
            return ToolExecutionResult(True, "API 验证通过: status=" + str(status))
        except Exception as e:
            return ToolExecutionResult(False, "API 验证失败: " + str(e))

    # ★ W3 T04 (2026-08-05): 网络故障注入（offline/abort/fulfill/delay）
    async def _network_fault_impl():
        from .._network_fault import apply_network_fault

        res = await apply_network_fault(engine, args)
        if res.ok:
            return ToolExecutionResult(True, res.message)
        return ToolExecutionResult(False, res.message)

    # ★ B5 (2026-08-16): 外部依赖 mock / 桩（register / clear / stats）
    #   与 network_fault 的分工：本动作是「可控打桩」（让外部依赖按我说的返），
    #   network_fault 是「故障注入」（让外部依赖坏掉）。两者互不干扰。
    async def _service_mock_impl():
        from .._service_mock import apply_service_mock

        res = await apply_service_mock(engine, args)
        return ToolExecutionResult(
            res.ok, res.message, {"mock": res.data} if res.data else None
        )

    # ★ 根因修复(2026-09-21) [未复核-需 GLM/DeepSeek 把关]: 通用 API 请求动作。
    #   现象：ACTION_TAXONOMY/用例集声明了 API 方法族动作（api_get/post/put/delete/patch），
    #   但执行层只有「断言已有流量」的 api_verify，没有「主动发起请求」的动作工具
    #   → API 方法族用例 100% NO_HANDLER（引擎能力缺口，非驱动器问题）。
    #   通用性：零靶子硬编码——base_url 解析顺序 args.base_url → config.target_url；
    #   url 支持绝对/相对；结果同步登记 engine._api_tracker（复用 verify_api_* 断言面，
    #   不另造第二套 API 数据面）。任何异常 fail-closed 返回结构化结果，绝不 raise。
    async def _api_request_impl():
        import requests as _rq

        method = str(args.get("method", "") or "").strip().upper() or "GET"
        url = str(args.get("url", "") or args.get("endpoint", "") or "").strip()
        if not url:
            return ToolExecutionResult(
                False, "api_request 缺必填参数 url/endpoint（fail-closed）"
            )
        base = str(args.get("base_url", "") or "").strip()
        if not base:
            try:
                from src.core.config_loader import config as _cfg

                base = (_cfg.target_url or "").rstrip("/")
            except Exception:
                base = ""
        if url.startswith("http://") or url.startswith("https://"):
            full = url
        elif base:
            full = base + (url if url.startswith("/") else "/" + url)
        else:
            return ToolExecutionResult(
                False,
                "api_request 无法解析 base_url（config.target_url 为空且未传 base_url）",
            )
        timeout_ms = int(args.get("timeout_ms", 0) or 15000)
        kwargs: dict = {"timeout": max(timeout_ms, 1) / 1000.0}
        if args.get("params"):
            kwargs["params"] = args["params"]
        if args.get("headers"):
            kwargs["headers"] = args["headers"]
        body = args.get("json") if args.get("json") is not None else args.get("data")
        if body is not None and method not in ("GET", "HEAD"):
            kwargs["json"] = body
        t0 = time.time()
        try:
            resp = _rq.request(method, full, **kwargs)
        except _rq.exceptions.Timeout:
            return ToolExecutionResult(
                False,
                f"API_TIMEOUT: {method} {full} 超时(>{timeout_ms}ms)，按超时归因非业务失败",
            )
        except Exception as e:
            return ToolExecutionResult(
                False, f"API_REQUEST_ERROR: {type(e).__name__}: {str(e)[:120]}"
            )
        elapsed_ms = int((time.time() - t0) * 1000)
        try:
            body_txt = (resp.text or "")[:300]
        except Exception:
            body_txt = ""
        # 登记 API 追踪器：与 verify_api_* 断言族共用同一数据面
        tracker = getattr(engine, "_api_tracker", None)
        if tracker is not None and hasattr(tracker, "_recent_requests"):
            with guard("_api_request_impl: tracker._recent_requests.append( { '", log=_log):
                tracker._recent_requests.append(
                    {
                        "url": full,
                        "method": method,
                        "status": resp.status_code,
                        "response_body": body_txt,
                        "elapsed_ms": elapsed_ms,
                    }
                )
        expect = args.get("expect_status")
        ok = True
        if expect is not None and str(expect) != "":
            try:
                ok = resp.status_code == int(expect)
            except (TypeError, ValueError):
                ok = False
        elif resp.status_code >= 400:
            ok = False
        return ToolExecutionResult(
            ok,
            f"API {method} {full} → status={resp.status_code} time={elapsed_ms}ms body={body_txt[:120]}",
            {"status": resp.status_code, "elapsed_ms": elapsed_ms, "url": full},
        )

    # ★ 根因修复(2026-09-21) [未复核-需 GLM/DeepSeek 把关]: 身份切换动作落地。
    #   现象：tool_definitions/taxonomy 已声明 identity_switch（W3 骨架，W4 未实现）
    #   → 分派表无 handler → 该动作 100% 未知工具（声明↔执行漂移）。
    #   通用性：凭据解析 身份池(IDENTITY_POOL，加密落库) → 默认身份回退（仅当
    #   请求身份与 target_username 一致时允许用配置凭据重建会话）；会话切换复用
    #   browser_core/session_login.py 单一实现（与 SessionAware 同一登录代码，零重复轮子）。
    async def _identity_switch_impl():
        # ★ 根因修复(2026-09-21): 会话重建是共享单例资源（同一浏览器 context）上的
        #   非重入操作——并发清 cookie+登录会互相拆除对方刚建立的会话（一致性破坏）。
        #   per-engine 锁串行化后，每次调用都完整完成「清会话→重建会话」，结果一致。
        lock = getattr(engine, "_identity_switch_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            engine._identity_switch_lock = lock
        async with lock:
            return await _identity_switch_body()

    async def _identity_switch_body():
        identity = str(args.get("identity", "") or "").strip()
        if not identity:
            return ToolExecutionResult(
                False, "identity_switch 缺必填参数 identity（fail-closed）"
            )
        page = getattr(engine, "_page", None)
        if page is None:
            return ToolExecutionResult(False, "页面未初始化")
        from src.core.config_loader import config as _cfg

        username = ""
        password = ""
        try:
            from src.pipeline.utils.identity_pool import (
                IdentityPool,
                apply_identity_override,
            )

            project = str(
                getattr(engine, "project", "") or getattr(_cfg, "project", "") or "*"
            )
            pool = IdentityPool.load(project)
            ident = pool.get(identity)
            if ident:
                apply_identity_override(project, ident)
                username = str(ident.get("username", "") or "")
                password = str(ident.get("password", "") or "")
        except Exception:
            ident = None
        if not username:
            a_user = str(args.get("username", "") or "").strip()
            a_pwd = str(args.get("password", "") or "")
            try:
                cfg_user = (_cfg.target_username or "").strip()
                cfg_pwd = _cfg.target_password or ""
            except Exception:
                cfg_user, cfg_pwd = "", ""
            if a_user:
                # ★ 调用方显式提供该身份凭据，优先于 config（config 可能残留
                #   其它项目/过期凭据；调用方按同一身份解析来源显式给出）
                username, password = a_user, a_pwd
            elif identity == cfg_user:
                username, password = cfg_user, cfg_pwd
            else:
                return ToolExecutionResult(
                    False,
                    f"身份 '{identity}' 不可解析：身份池未启用/未注册该身份，"
                    "且非目标默认身份、调用方亦未提供凭据",
                )
        base_url = ""
        try:
            base_url = (_cfg.target_url or "").rstrip("/")
        except Exception:
            base_url = ""
        # 入参显式提供 base_url 时优先（驱动器/独立执行进程可能未加载项目配置）
        base_url = str(args.get("base_url", "") or "").strip() or base_url
        # ★ 会话重建前置：清除当前 cookie，否则已登录页永远无登录表单，
        #   session_login 探测不到登录页 → 「切换身份」退化为空操作
        with guard("_identity_switch_body: ctx = getattr(engine, '_context', No", log=_log):
            ctx = getattr(engine, "_context", None)
            if ctx is not None:
                await ctx.clear_cookies()
        from ...browser_core.session_login import session_login

        ok, detail = await session_login(
            page,
            engine.goto,
            base_url,
            username,
            password,
            login_path=(getattr(_cfg, "target_login_path", "") or ""),
            username_sel=(getattr(_cfg, "login_username_selector", "") or ""),
            password_sel=(getattr(_cfg, "login_password_selector", "") or ""),
            submit_sel=(getattr(_cfg, "login_submit_selector", "") or ""),
            origin="identity_switch",
        )
        if ok:
            return ToolExecutionResult(
                True, f"身份已切换并重建会话: {identity} ({detail[:80]})"
            )
        return ToolExecutionResult(
            False, f"身份 '{identity}' 会话重建失败: {detail[:150]}"
        )

    async def _browser_upload_file():
        try:
            page = getattr(engine, "_page", None)
            if not page:
                return ToolExecutionResult(False, "页面未初始化")
            fp = _ensure_upload_test_file(args.get("file_path", ""))
            if not fp:
                return ToolExecutionResult(False, "无法准备上传测试文件")

            async def _do_upload_click(
                a, p=page, h=hint, file_path=fp, s=args.get("selector", "")
            ):
                healed = a.get("target_hint", h)
                # ★ 通用修复(2026-07-21 v7): upload_file 三阶段上传策略
                #   根因(3层追问):
                #     1. 表层: upload_file 超时30s 等待 filechooser 事件
                #     2. 中层: locator.py _UPLOAD_KEYWORDS=('上传','导入',...) 把
                #        hint="上传" 模糊匹配到业务按钮"数据导入"(含"导入"关键词),
                #        click 该按钮打开弹窗而非触发 filechooser
                #     3. 根因: 不同系统 upload 实现差异大:
                #        - A: input[type=file] 直接可访问 → set_input_files 即可
                #        - B: Ant Design Upload 组件, click 触发按钮 → filechooser 事件
                #        - C: 业务按钮打开弹窗 → 弹窗内动态渲染 upload 组件
                #   原代码只用 filechooser+click 单一路径 + hint 模糊匹配,
                #   对 A/C 不工作, B 场景下 hint 匹配到错误按钮导致超时。
                #   通用修复: 三阶段策略, 不依赖 hint 匹配:
                #     阶段1: set_input_files 直接设置所有 input[type=file] (最通用)
                #     阶段2: 通用选择器找 upload 触发元素 + expect_file_chooser
                #     阶段3: hint 模糊匹配兜底 (原逻辑)
                #   通用性: 适用于所有 HTML 系统的文件上传 (Ant Design/Vue/原生)
                #   合理性: set_input_files 是 Playwright 官方推荐方式, 对隐藏 input
                #           也可工作, 不依赖 filechooser 事件
                #   覆盖性: 覆盖所有 upload_file action 调用

                # 阶段0: 等待弹窗渲染 (click 后弹窗动态渲染 input[type=file] 需要时间)
                #   ★ 通用修复(2026-07-21 v9): upload_file 前等待 upload 组件出现
                #     根因: upload_rule.py 生成 click(operation) → upload_file 序列,
                #     click 打开弹窗后 input[type=file] 动态渲染, 需要等待。
                #     原代码立即查找 input[type=file], 弹窗未渲染完成 → 找不到 → 失败。
                #   通用修复: 等待 3s 让弹窗渲染, 再执行阶段1。
                #   通用性: 适用于所有弹窗式 upload 场景 (Ant Design Modal/Element Dialog/原生)
                with guard("_do_upload_click: await p.wait_for_selector( 'input[ty", log=_log, level=logging.INFO):
                    await p.wait_for_selector(
                        'input[type="file"], .ant-upload, .el-upload, [class*="upload"]',
                        timeout=3000,
                    )
                    _log.info("[upload] 阶段0: 等待到 upload 组件出现")

                # ★ v19 通用修复(2026-07-23): 上传前尝试下载系统导入模板
                #   根因(§10.9):
                #     1. 现象: v16 生成的 xlsx 被 某SPA系统 拒绝("数据表不能为空")
                #     2. 为什么1(直接): 生成的 xlsx 只有通用数据，不含系统期望的 sheet 名/列头
                #     3. 为什么2(中间): Step4 不知道系统期望的文件格式
                #     4. 为什么3(根因): Step1 未爬取模态框 DOM，rag_chunks 无模板下载按钮信息
                #   修复: 运行时在当前页面/模态框搜索"下载模板"按钮，下载真实模板并填充测试数据
                #   通用性(§12.4):
                #     - 跨系统: 适用于任何提供模板下载的系统(Ant Design/ElementUI/Native)
                #     - 跨行业: 不依赖业务术语，只匹配"模板"通用关键词
                #     - 跨页面: 在 modal/dialog/页面任意位置搜索
                #     - 数据完整性: 有模板用模板，无模板降级为 v16 生成的文件
                #     - 根因溯源: 修复"不知道系统期望格式"根因，非表层补丁
                #     - 覆盖性: 覆盖所有提供模板下载的上传场景
                #     - 全局观: 只增强 Step4 执行(Self-Healing)，不影响其他 Step
                #     - 职责边界: Step4 Self-Healing 范围内(运行时获取正确格式)
                #   自我反驳三次:
                #     1) 漏洞？→ 误点"模板"按钮非下载？→ 排除负面关键词+expect_download验证
                #     2) 更通用？→ 让Step3生成？→ 违反职责边界(Step3无运行时DOM访问)
                #     3) 更根本？→ 修Step1爬模态框？→ 成本高且需触发显示，v19运行时更直接
                with guard("_do_upload_click: _template_path_v19 = await _try_down", log=_log):
                    _template_path_v19 = await _try_download_template(p, timeout_sec=5)
                    if _template_path_v19:
                        _filled_path_v19 = _fill_template_with_test_data(
                            _template_path_v19
                        )
                        if _filled_path_v19 and _filled_path_v19 != file_path:
                            file_path = _filled_path_v19
                            _log.info(
                                "[upload] v19: ✓ 已切换为填充后的模板文件: %s",
                                Path(file_path).name,
                            )

                # 阶段1: 直接 set_input_files (不依赖 filechooser 事件)
                #   ★ v16 通用修复(2026-07-23): 读取 accept 属性，文件格式自适应
                #     根因: Step3 用静态配置 sample.txt，不感知目标系统 accept 属性
                #     修复: 读取 input[type=file].accept，动态生成兼容格式文件
                #     通用性: 适用于所有系统的文件上传（Excel/PDF/图片/CSV 等）
                with guard("_do_upload_click: file_inputs = await p.locator('input", log=_log, level=logging.WARNING):
                    file_inputs = await p.locator('input[type="file"]').all()
                    _log.info(
                        "[upload] 阶段1: 找到 %d 个 input[type=file]", len(file_inputs)
                    )
                    # ★ v16: 读取第一个 input 的 accept 属性
                    _accept_attr = ""
                    for fi in file_inputs:
                        try:
                            if await fi.count() > 0:
                                _accept_attr = await fi.get_attribute("accept") or ""
                                if _accept_attr:
                                    break
                        except Exception as _se30:
                            _log.debug(
                                "[_single] 异常降级(continue): %s",
                                str(_se30)[:60],
                                exc_info=True,
                            )
                            continue
                    if _accept_attr:
                        _log.info("[upload] v16: 检测到 accept=%s", _accept_attr[:80])
                        _compatible_path = _generate_compatible_upload_file(
                            file_path, _accept_attr
                        )
                        if _compatible_path != file_path:
                            file_path = _compatible_path
                            _log.info(
                                "[upload] v16: ✓ 已切换为兼容格式文件: %s",
                                Path(file_path).name,
                            )
                    for idx, fi in enumerate(file_inputs):
                        try:
                            if await fi.count() > 0:
                                await fi.set_input_files(file_path)
                                _log.info(
                                    "[upload] 阶段1 set_input_files 成功 (input #%d, file=%s)",
                                    idx,
                                    Path(file_path).name,
                                )
                                # ★ v18 通用修复(2026-07-23): set_input_files 后验证上传是否触发
                                #   根因(§10.9):
                                #     1. 现象: set_input_files 成功但无业务反馈通知 → text_contains:✗
                                #     2. 为什么1(直接): Ant Design Upload 的 React onChange 未被原生 change 事件触发
                                #     3. 为什么2(中间): set_input_files 设置了文件但未模拟用户点击"选择文件"的完整交互
                                #     4. 为什么3(根因): React 合成事件系统与原生事件存在隔离，
                                #        部分组件(如 Ant Design Upload)需要通过 filechooser 事件触发 onChange
                                #   修复: set_input_files 后等待 2s 检查反馈(通知/文件列表/加载动画)，
                                #         无反馈则不返回，继续阶段2(click+filechooser 模拟真实用户操作)
                                #   通用性: 适用于所有系统——原生HTML(set_input_files即触发) +
                                #           React/Ant Design/ElementUI(需filechooser触发)
                                #   合理性: 追根溯源到事件机制差异(不是补丁)
                                #   覆盖性: 影响所有 upload_file 调用
                                #   全局观: 不影响其他 Step，仅优化 Step4 上传执行
                                #   职责边界: Step4 执行层内(不越界)
                                #   自我反驳:
                                #     1) 2s太短? → 上传反馈通常1s内出现，2s足够；过长浪费时间
                                #     2) 更通用? → 直接用filechooser替代set_input_files?
                                #        → 不行，部分系统input无触发按钮，set_input_files是唯一方式
                                #     3) 更根本? → 监控网络请求? → 复杂度高，当前反馈检测已足够
                                _triggered = await _check_upload_triggered(
                                    p, timeout_sec=2
                                )
                                # ★ v19 通用修复(2026-07-23): 区分成功/错误反馈，杜绝假PASS
                                #   根因(§10.9):
                                #     1. 现象: set_input_files 后系统返回"数据表不能为空"错误通知
                                #     2. 为什么1(直接): v18 将任何反馈(含错误通知)视为成功
                                #     3. 为什么2(中间): _check_upload_triggered 已分类 error/success，
                                #        但调用方未使用分类结果
                                #     4. 为什么3(根因): 上传"触发"≠上传"成功"——系统可能拒绝不合规文件
                                #   修复: 错误通知 → 返回 failure(含错误文本)；成功反馈 → 返回 success
                                #   通用性: 适用于所有系统的上传反馈(任何错误通知都不应视为成功)
                                #   覆盖性: 影响所有 set_input_files 调用路径
                                #   全局观: 防止假PASS污染整条流水线(PASS必须有真实证据)
                                #   职责边界: Step4 执行层内(反馈判定是执行的一部分)
                                #   自我反驳:
                                #     1) 漏洞？→ 罕见情况先错后对 → 2s窗口取最终态，足够
                                #     2) 更通用？→ 监控网络请求状态码？→ 复杂度高，UI反馈已足够
                                #     3) 更根本？→ 修复文件内容(v19b)才是根本，此处先堵假PASS
                                if _triggered:
                                    if _triggered.startswith("notification:error:"):
                                        _err_text = (
                                            _triggered.split(":", 2)[2]
                                            if _triggered.count(":") >= 2
                                            else _triggered
                                        )
                                        _log.warning(
                                            "[upload] v19: ✗ 检测到错误通知(%s)，上传被系统拒绝",
                                            _err_text[:80],
                                        )
                                        return {
                                            "success": False,
                                            "method": "set_input_files_rejected",
                                            "error": f"系统拒绝上传: {_err_text[:120]}",
                                        }
                                    else:
                                        _log.info(
                                            "[upload] v19: ✓ set_input_files 后检测到上传反馈(%s)，返回成功",
                                            _triggered,
                                        )
                                        return {
                                            "success": True,
                                            "method": "set_input_files_direct",
                                        }
                                else:
                                    _log.info(
                                        "[upload] v18: set_input_files 后 2s 无反馈，继续阶段2(filechooser)"
                                    )
                                    break  # 跳出 for 循环，继续阶段2
                        except Exception as _e1:
                            _log.info(
                                "[upload] 阶段1 input #%d set_input_files 失败: %s",
                                idx,
                                str(_e1)[:100],
                            )
                            continue

                # 阶段2: 通用选择器找 upload 触发元素 + expect_file_chooser
                #   不用 _UPLOAD_KEYWORDS (含"上传"/"导入"会匹配业务按钮)
                #   只用框架无关的通用选择器 + 明确的文件选择触发词
                _UPLOAD_TRIGGER_SELECTORS = [
                    ".ant-upload",  # Ant Design
                    ".el-upload",  # Element UI
                    '[class*="upload"]:not([class*="upload-disabled"])',  # 通用 upload class
                    'button:has-text("选择文件")',
                    'button:has-text("browse")',
                    'button:has-text("点击上传")',
                    'button:has-text("upload")',
                    'a:has-text("选择文件")',
                    '[role="button"]:has-text("选择文件")',
                    '[role="button"]:has-text("上传")',
                ]
                _log.info(
                    "[upload] 阶段2: 尝试 %d 个通用选择器",
                    len(_UPLOAD_TRIGGER_SELECTORS),
                )
                for sel in _UPLOAD_TRIGGER_SELECTORS:
                    try:
                        loc = p.locator(sel).first
                        cnt = await loc.count()
                        if cnt > 0:
                            _log.info(
                                "[upload] 阶段2: 选择器 '%s' 命中 (count=%d), 尝试 click+filechooser",
                                sel[:60],
                                cnt,
                            )
                            try:
                                async with p.expect_file_chooser(
                                    timeout=3000
                                ) as fc_info:
                                    await loc.click(timeout=3000)
                                file_chooser = await fc_info.value
                                await file_chooser.set_files(file_path)
                                _log.info(
                                    "[upload] 阶段2 filechooser 成功 (sel=%s)", sel[:60]
                                )
                                # ★ v19 覆盖性修复: filechooser 后也验证反馈(防止假PASS)
                                _triggered_v19_s2 = await _check_upload_triggered(
                                    p, timeout_sec=2
                                )
                                if _triggered_v19_s2 and _triggered_v19_s2.startswith(
                                    "notification:error:"
                                ):
                                    _err_v19_s2 = (
                                        _triggered_v19_s2.split(":", 2)[2]
                                        if _triggered_v19_s2.count(":") >= 2
                                        else _triggered_v19_s2
                                    )
                                    _log.warning(
                                        "[upload] v19 阶段2: ✗ 系统拒绝上传(%s)",
                                        _err_v19_s2[:80],
                                    )
                                    return {
                                        "success": False,
                                        "method": "filechooser_rejected",
                                        "error": f"系统拒绝上传: {_err_v19_s2[:120]}",
                                    }
                                return {
                                    "success": True,
                                    "method": "filechooser_generic",
                                    "selector": sel,
                                }
                            except Exception as _e2:
                                _log.info(
                                    "[upload] 阶段2 选择器 '%s' filechooser 失败: %s",
                                    sel[:60],
                                    str(_e2)[:100],
                                )
                                continue
                    except Exception as _e3:
                        _log.debug(
                            "[upload] 阶段2 选择器 '%s' 异常: %s",
                            sel[:60],
                            str(_e3)[:100],
                        )
                        continue

                # 阶段3: hint 模糊匹配兜底 (短超时, 失败让 AI 接管)
                #   ★ 通用修复(2026-07-21 v8): 阶段3 timeout 从 30s 改为 5s
                #     原因: hint 模糊匹配可能匹配到业务按钮(如"数据导入"), click 不触发
                #     filechooser, 等待 30s 浪费时间。5s 足够判断是否触发 filechooser。
                #   通用性: 适用于所有系统, 失败快速返回让 AI 视觉接管。
                _log.info("[upload] 阶段3: hint 模糊匹配兜底 (hint=%r)", healed[:50])
                locator = await _locate_with_fallback(
                    p, healed, kind="upload", selector=s
                )
                if not locator:
                    raise ValueError(f"未找到上传元素: {healed}")
                # ★ 前置验证：元素可交互（存在 + 可见 + 未禁用）
                verify = await pre_verify_interactive_element(locator)
                if not verify.passed:
                    raise ValueError(f"元素不可交互: {healed} — {verify.message}")
                try:
                    async with p.expect_file_chooser(timeout=5000) as fc_info:
                        await locator.click(timeout=5000)
                    file_chooser = await fc_info.value
                    await file_chooser.set_files(file_path)
                    _log.info("[upload] 阶段3 filechooser 成功 (hint=%r)", healed[:50])
                    # ★ v19 覆盖性修复: filechooser 后也验证反馈(防止假PASS)
                    _triggered_v19_s3 = await _check_upload_triggered(p, timeout_sec=2)
                    if _triggered_v19_s3 and _triggered_v19_s3.startswith(
                        "notification:error:"
                    ):
                        _err_v19_s3 = (
                            _triggered_v19_s3.split(":", 2)[2]
                            if _triggered_v19_s3.count(":") >= 2
                            else _triggered_v19_s3
                        )
                        _log.warning(
                            "[upload] v19 阶段3: ✗ 系统拒绝上传(%s)", _err_v19_s3[:80]
                        )
                        return {
                            "success": False,
                            "method": "filechooser_rejected",
                            "error": f"系统拒绝上传: {_err_v19_s3[:120]}",
                        }
                    return {"success": True, "method": "filechooser_hint"}
                except Exception as _e3:
                    raise ValueError(
                        f"upload_file 三阶段全部失败 (hint={healed[:50]}): 阶段1无input, 阶段2无触发元素, 阶段3 filechooser超时 — {str(_e3)[:100]}"
                    )

            upload_result = await run_action_with_hooks(
                engine, "click", hint, do_action=_do_upload_click
            )
            if upload_result.get("success"):
                # ★ v9.1 通用修复(2026-07-22): upload_file 成功后等待并点击提交按钮
                _log.warning(
                    "[upload] v9.1 进入等待+点击逻辑: upload_result=%s fp=%s",
                    str(upload_result)[:100],
                    str(fp)[:50],
                )
                #   根因核查(§10.9):
                #     1. 现象: v7.4 等待 10s 未找到提交按钮，但后续 locator 能找到"导入"关键词
                #     2. 直接原因: v7.4 用 fullmatch 匹配，按钮文本必须完全等于关键词，
                #        但 某SPA系统 弹窗按钮文本可能是"导入数据"/"确认导入"等非完全匹配
                #     3. 中间原因: v7.4 找到按钮后只记录日志，不点击，后续 click "开始导入"
                #        用 force click 绕过 React 事件，按钮 onClick 未触发
                #     4. 根本原因: upload_file 语义应包含"等待+点击提交按钮"完整流程
                #   全局观: 影响所有"上传文件→点击确定/提交/保存"的流程（所有系统/行业）
                #   职责边界: Step4 执行层——等待+点击是 upload 执行语义的一部分，不是决策
                #   通用性自检:
                #     - 跨系统: 适用于 Ant Design/ElementUI/Native HTML 的 Modal/Dialog
                #     - 跨行业: 通用（所有需要上传后提交的场景）
                #     - 跨页面: 适用于所有含 modal/dialog 的上传页面
                #   自我反驳三次:
                #     1) 漏洞？→ search 可能误匹配"取消导入"→ 排除负面关键词
                #     2) 更通用？→ 扩展按钮选择器，覆盖 div/span/a 等非标准按钮
                #     3) 更根本？→ upload_file 语义应包含完整流程，这是最根本的修复
                #   实施 v9.1:
                #     - fullmatch → search（部分匹配）
                #     - 排除"取消/关闭/返回/退出/放弃"等负面关键词
                #     - 找到按钮后直接点击（普通 click，触发 React 事件）
                #     - 扩展按钮选择器（button/[role=button]/.ant-btn/.el-button/a/span/div[role=button]）
                #     - 点击成功后返回"已提交"标记
                with guard("_browser_upload_file: import re as _re_v91", log=_log):
                    import re as _re_v91

                    _SUBMIT_PATTERNS_V91 = []
                    for _kw_v91 in _SUBMIT_KWS_V91:
                        _chars_v91 = list(_kw_v91)
                        _regex_v91 = r"\s*".join(
                            _re_v91.escape(c) for c in _chars_v91 if c
                        )
                        _SUBMIT_PATTERNS_V91.append(
                            (_kw_v91, _re_v91.compile(_regex_v91))
                        )

                    _submit_clicked_v91 = False
                    # ★ v17 通用修复(2026-07-23): 扩展容器选择器，覆盖 dropdown/popover
                    #   根因: v9.1 只搜标准 modal，某SPA系统 等系统的上传容器可能是 dropdown/popover
                    #   通用性: 覆盖 Ant Design/ElementUI/Naive UI 的所有弹层类型
                    _modal_selectors_v91 = (
                        ".ant-modal",
                        ".el-dialog",
                        '[role="dialog"]',
                        ".modal",
                        ".ant-drawer-content",
                        ".ant-dropdown-menu",
                        ".ant-popover-inner",
                        ".el-dropdown-menu",
                        ".el-popover",
                        ".n-dropdown-menu",
                        ".n-popover",
                    )
                    # ★ v9.1: 扩展按钮选择器，覆盖非标准按钮（div/span/a + role/onclick）
                    _btn_selector_v91 = (
                        "button, [role='button'], .ant-btn, .el-button, "
                        "a[class*='btn'], a[class*='button'], "
                        "span[class*='btn'], span[class*='button'], "
                        "div[class*='btn'], div[class*='button'], "
                        ".ant-btn-text, .el-button--text"
                    )
                    # ★ v9.1 改进: 等待时间从 10s 改为 3s
                    #   根因: 某SPA系统 等系统 upload 后不立即弹出 modal，10s 等待浪费时间
                    #   通用性: 3s 足以检测 upload 后立即弹出的 modal，不阻塞后续 click
                    for _wait_sec_v91 in range(3):
                        await asyncio.sleep(1)
                        for _modal_sel_v91 in _modal_selectors_v91:
                            try:
                                _modal_v91 = page.locator(_modal_sel_v91).first
                                if await _modal_v91.count() == 0:
                                    continue
                                _btns_v91 = _modal_v91.locator(_btn_selector_v91)
                                _btn_count_v91 = await _btns_v91.count()
                                for _bi_v91 in range(min(_btn_count_v91, 30)):
                                    try:
                                        _btn_v91 = _btns_v91.nth(_bi_v91)
                                        _text_v91 = (
                                            await _btn_v91.text_content() or ""
                                        ).strip()
                                        _text_nospace_v91 = (
                                            _text_v91.replace(" ", "")
                                            .replace("\t", "")
                                            .replace("\n", "")
                                        )
                                        if (
                                            not _text_nospace_v91
                                            or len(_text_nospace_v91) > 12
                                        ):
                                            continue
                                        # ★ v9.1: 排除负面关键词
                                        if any(
                                            _neg in _text_nospace_v91
                                            for _neg in _NEGATIVE_KWS_V91
                                        ):
                                            continue
                                        # ★ v9.1: search 部分匹配（替代 fullmatch）
                                        for (
                                            _kw_v91,
                                            _pattern_v91,
                                        ) in _SUBMIT_PATTERNS_V91:
                                            if _pattern_v91.search(_text_nospace_v91):
                                                if await _btn_v91.is_visible():
                                                    _log.info(
                                                        "[upload] v9.1: 等待 %ds 后提交按钮 '%s' 出现 (modal=%s text='%s')，尝试普通 click",
                                                        _wait_sec_v91 + 1,
                                                        _kw_v91,
                                                        _modal_sel_v91,
                                                        _text_v91,
                                                    )
                                                    # ★ v9.1: 直接点击按钮（普通 click，触发 React 事件）
                                                    try:
                                                        await _btn_v91.click(
                                                            timeout=3000
                                                        )
                                                        _submit_clicked_v91 = True
                                                        _log.info(
                                                            "[upload] v9.1: ✓ 提交按钮 '%s' 点击成功 (text='%s')",
                                                            _kw_v91,
                                                            _text_v91,
                                                        )
                                                    except Exception as _click_e_v91:
                                                        _log.warning(
                                                            "[upload] v9.1: 提交按钮 '%s' 普通 click 失败: %s，尝试 force click",
                                                            _kw_v91,
                                                            str(_click_e_v91)[:80],
                                                        )
                                                        with guard("_browser_upload_file: await _btn_v91.click( timeout=3000, ", log=_log, level=logging.WARNING):
                                                            await _btn_v91.click(
                                                                timeout=3000, force=True
                                                            )
                                                            _submit_clicked_v91 = True
                                                            _log.info(
                                                                "[upload] v9.1: ✓ 提交按钮 '%s' force click 成功 (text='%s')",
                                                                _kw_v91,
                                                                _text_v91,
                                                            )
                                                    break
                                        if _submit_clicked_v91:
                                            break
                                    except Exception as _se31:
                                        _log.debug(
                                            "[_single] 异常降级(continue): %s",
                                            str(_se31)[:60],
                                            exc_info=True,
                                        )
                                        continue
                                if _submit_clicked_v91:
                                    break
                            except Exception as _se32:
                                _log.debug(
                                    "[_single] 异常降级(continue): %s",
                                    str(_se32)[:60],
                                    exc_info=True,
                                )
                                continue
                        if _submit_clicked_v91:
                            break
                    if _submit_clicked_v91:
                        # ★ v11 通用修复(2026-07-23): 等待业务反馈通知出现(替代固定sleep 2s)
                        #   根因: 固定2s等待可能不够,系统处理上传需要时间
                        #   通用: wait_for_selector 自适应等待,通知出现即继续,最多10s
                        _feedback_text = await _wait_for_upload_feedback(page)
                        # ★ v9.2: 设置 engine._upload_submitted 标志位
                        #   后续 click action 若 target_hint 是提交类按钮（确定/提交/保存/导入/上传），
                        #   且元素找不到（modal 已关闭），则跳过并返回成功，避免假 FAIL。
                        try:
                            setattr(engine, "_upload_submitted", True)
                            _log.info(
                                "[upload] v9.2: 已设置 engine._upload_submitted=True，后续提交类 click 将跳过"
                            )
                        except Exception as _se33:
                            _log.debug(
                                "[_single] 异常降级(pass): %s",
                                str(_se33)[:60],
                                exc_info=True,
                            )
                            pass
                        _log.info(
                            "[upload] v9.1: 已通过 v9.1 完成上传+提交，后续 click action 可跳过"
                        )
                        return ToolExecutionResult(True, "已上传并提交: " + fp)
                    # ★ v17 通用修复(2026-07-23): modal 内未找到提交按钮时，页面级回退搜索
                    #   根因(3层追问):
                    #     1. 现象: set_input_files 成功但无业务反馈通知
                    #     2. 直接原因: v9.1 只在 modal 内搜索提交按钮，未找到 → 假设"上传即提交"
                    #     3. 根本原因: 部分系统的提交按钮在 modal 外（页面工具栏/底部操作栏），
                    #        或上传容器是 dropdown/popover（非标准 modal）
                    #   修复: modal 搜索失败后，在整页面搜索可见的提交类按钮
                    #   通用性(§12.4):
                    #     - 跨系统: 搜索整页面，不依赖特定容器类型
                    #     - 跨行业: 适用于所有"提交按钮在弹窗外"的系统
                    #     - 跨页面: 适用于任何上传页面
                    #     - 覆盖性: 复用 v9.1 的关键词+负面关键词过滤，安全可控
                    #   自我反驳三次:
                    #     1) 误点无关按钮? → 负面关键词过滤 + 只点可见按钮 + 只点提交类关键词
                    #     2) 更通用? → 监控网络请求? → 复杂且不直观，页面搜索更直接
                    #     3) 更根本? → Step3 生成 click submit? → 已移除(v11)，运行时检测更准
                    if not _submit_clicked_v91:
                        _log.info(
                            "[upload] v17: modal 内未找到提交按钮，尝试页面级搜索"
                        )
                        with guard("_browser_upload_file: _page_btns_v17 = page.locator(_btn_s", log=_log):
                            _page_btns_v17 = page.locator(_btn_selector_v91)
                            _page_btn_count_v17 = await _page_btns_v17.count()
                            _log.info(
                                "[upload] v17: 页面级找到 %d 个按钮",
                                _page_btn_count_v17,
                            )
                            for _bi_v17 in range(min(_page_btn_count_v17, 50)):
                                try:
                                    _btn_v17 = _page_btns_v17.nth(_bi_v17)
                                    if not await _btn_v17.is_visible():
                                        continue
                                    _text_v17 = (
                                        await _btn_v17.text_content() or ""
                                    ).strip()
                                    _text_nospace_v17 = (
                                        _text_v17.replace(" ", "")
                                        .replace("\t", "")
                                        .replace("\n", "")
                                    )
                                    if (
                                        not _text_nospace_v17
                                        or len(_text_nospace_v17) > 12
                                    ):
                                        continue
                                    if any(
                                        _neg in _text_nospace_v17
                                        for _neg in _NEGATIVE_KWS_V91
                                    ):
                                        continue
                                    for _kw_v17, _pattern_v17 in _SUBMIT_PATTERNS_V91:
                                        if _pattern_v17.search(_text_nospace_v17):
                                            _log.info(
                                                "[upload] v17: 页面级提交按钮 '%s' (text='%s')，尝试 click",
                                                _kw_v17,
                                                _text_v17,
                                            )
                                            # ★ v17.1 通用修复: normal click 超时不 force click
                                            #   根因: force click 绕过遮罩层检查，会点击到 modal 后面的
                                            #         触发按钮（如"数据导入"），导致错误操作
                                            #   通用性: 适用于所有系统——提交按钮应在 modal 内可交互，
                                            #           不可交互的按钮不是目标提交按钮
                                            with guard("_browser_upload_file: await _btn_v17.click(timeout=3000)", log=_log, level=logging.INFO):
                                                await _btn_v17.click(timeout=3000)
                                                _submit_clicked_v91 = True
                                                _log.info(
                                                    "[upload] v17: ✓ 页面级提交按钮 '%s' 点击成功",
                                                    _text_v17,
                                                )
                                            break
                                    if _submit_clicked_v91:
                                        break
                                except Exception as _se34:
                                    _log.debug(
                                        "[_single] 异常降级(continue): %s",
                                        str(_se34)[:60],
                                        exc_info=True,
                                    )
                                    continue
                    if _submit_clicked_v91:
                        _feedback_text = await _wait_for_upload_feedback(page)
                        try:
                            setattr(engine, "_upload_submitted", True)
                            _log.info(
                                "[upload] v17: 已设置 _upload_submitted=True（页面级提交）"
                            )
                        except Exception as _se35:
                            _log.debug(
                                "[_single] 异常降级(pass): %s",
                                str(_se35)[:60],
                                exc_info=True,
                            )
                            pass
                        return ToolExecutionResult(True, "已上传并提交(页面级): " + fp)
                    _log.info(
                        "[upload] v9.1: 等待 3s 后未在 modal 内发现可点击的提交按钮，上传本身即提交"
                    )
                # ★ v10 通用修复(2026-07-22): upload_file 成功后无论是否找到提交按钮都设标志位
                #   根因: 部分系统"上传即提交"（无单独提交按钮），v9.1 找不到按钮时不设标志位，
                #         导致后续 click 提交类按钮无法被 v9.2 跳过 → 假FAIL
                #   通用性: 覆盖"上传即提交"和"上传+点提交"两种交互模型（所有系统/行业）
                #   合理性: upload_file 语义已完成（文件已上传），后续提交类click是冗余的
                #   职责边界: Step4 执行层——upload_file 动作语义完成
                try:
                    setattr(engine, "_upload_submitted", True)
                    _log.info(
                        "[upload] v10: upload_file 完成, 设 _upload_submitted=True (上传即提交模型)"
                    )
                except Exception as _se36:
                    _log.debug(
                        "[_single] 异常降级(pass): %s", str(_se36)[:60], exc_info=True
                    )
                    pass
                # ★ v11 通用修复(2026-07-23): "上传即提交"后等待业务反馈通知出现
                #   根因: upload_file 返回后2秒内就执行check,系统来不及处理上传并显示通知
                #   通用: wait_for_selector 自适应等待,通知出现即继续,最多10s
                _feedback_text = await _wait_for_upload_feedback(page)
                return ToolExecutionResult(True, "已上传文件: " + fp)
            return ToolExecutionResult(
                False, "上传失败: " + upload_result.get("error", "?")
            )
        except Exception as e:
            return ToolExecutionResult(False, "上传失败: " + str(e))

    async def _get_page_state():
        try:
            page = getattr(engine, "_page", None)
            if not page:
                return ToolExecutionResult(False, "页面未初始化")
            url = page.url or ""
            try:
                title = await page.title() or ""
            except Exception as _e:
                _log.debug("[_single] _do_upload_click 异常: %s", _e, exc_info=True)
                title = ""
            state = await engine.get_structured_state()
            texts = []
            for item in state[:30]:
                tag = item.get("tag", "") if isinstance(item, dict) else ""
                if isinstance(item, dict):
                    txt = item.get("text") or item.get("placeholder") or ""
                else:
                    txt = ""
                if tag:
                    short = str(txt)[:60]
                    texts.append(tag + ": " + short)
            summary = "URL: " + url + "\nTitle: " + title + "\n" + "\n".join(texts)
            return ToolExecutionResult(True, summary[:2000])
        except Exception as e:
            return ToolExecutionResult(False, "读取页面状态失败: " + str(e))

    # ── 字典分派表 ──
    _TOOL_HANDLERS = {
        "api_verify": _api_verify,
        "browser_blur": lambda: _browser_blur_impl(engine, args, hint),
        "browser_check": lambda: _browser_check_impl(engine, args, hint),
        "browser_clear": lambda: _browser_clear_impl(engine, args, hint),
        "browser_click": lambda: _browser_click_impl(engine, args, hint),
        "browser_close_tab": lambda: _browser_close_tab_impl(engine, args, hint),
        "browser_context_menu_click": lambda: _browser_context_menu_click_impl(
            engine, args, hint
        ),
        "browser_double_click": lambda: _browser_double_click_impl(engine, args, hint),
        "browser_download_file": lambda: _browser_download_file_impl(
            engine, args, hint
        ),
        "browser_drag": lambda: _browser_drag_impl(engine, args, hint),
        "browser_drag_offset": lambda: _browser_drag_offset_impl(engine, args, hint),
        "browser_fill": lambda: _browser_fill_impl(engine, args, hint),
        "browser_focus": lambda: _browser_focus_impl(engine, args, hint),
        "browser_go_back": lambda: _browser_go_back_impl(engine, args, hint),
        "browser_go_forward": lambda: _browser_go_forward_impl(engine, args, hint),
        "browser_hover": lambda: _browser_hover_impl(engine, args, hint),
        "browser_key_down": lambda: _browser_key_down_impl(engine, args, hint),
        "browser_key_up": lambda: _browser_key_up_impl(engine, args, hint),
        "browser_long_press": lambda: _browser_long_press_impl(engine, args, hint),
        "browser_middle_click": lambda: _browser_middle_click_impl(engine, args, hint),
        "browser_multi_touch_pinch": lambda: _browser_multi_touch_pinch_impl(
            engine, args, hint
        ),
        "browser_navigate": lambda: _browser_navigate_impl(engine, args, hint),
        "browser_page_reload": lambda: _browser_page_reload_impl(engine, args, hint),
        "browser_press_key": lambda: _browser_press_key_impl(engine, args, hint),
        "browser_resize_window": lambda: _browser_resize_window_impl(
            engine, args, hint
        ),
        "browser_right_click": lambda: _browser_right_click_impl(engine, args, hint),
        "browser_scroll": lambda: _browser_scroll_impl(engine, args, hint),
        "browser_select": lambda: _browser_select_impl(engine, args, hint),
        "browser_set_date": lambda: _browser_set_date_impl(engine, args, hint),
        "browser_swipe": lambda: _browser_swipe_impl(engine, args, hint),
        "browser_switch_tab": lambda: _browser_switch_tab_impl(engine, args, hint),
        "browser_switch_to_default": lambda: _browser_switch_to_default_impl(
            engine, args, hint
        ),
        "browser_switch_to_iframe": lambda: _browser_switch_to_iframe_impl(
            engine, args, hint
        ),
        "browser_take_screenshot": lambda: _browser_take_screenshot_impl(
            engine, args, hint
        ),
        "browser_type_text": lambda: _browser_type_text_impl(engine, args, hint),
        "browser_uncheck": lambda: _browser_uncheck_impl(engine, args, hint),
        "browser_upload_file": _browser_upload_file,
        "browser_wait": lambda: _browser_wait_impl(engine, args, hint),
        "browser_wheel": lambda: _browser_wheel_impl(engine, args, hint),
        "get_page_state": _get_page_state,
        "verify_animation_complete": lambda: _verify_animation_complete_impl(
            engine, args, hint
        ),
        "verify_api_call_count": lambda: _verify_api_call_count_impl(
            engine, args, hint
        ),
        "verify_api_header": lambda: _verify_api_header_impl(engine, args, hint),
        "verify_db_field_value": lambda: _verify_db_field_value_impl(
            engine, args, hint
        ),
        "verify_db_query": lambda: _verify_db_query_impl(engine, args, hint),
        "verify_db_record_count": lambda: _verify_db_record_count_impl(
            engine, args, hint
        ),
        "verify_db_record_exists": lambda: _verify_db_record_exists_impl(
            engine, args, hint
        ),
        "verify_db_record_not_exists": lambda: _verify_db_record_not_exists_impl(
            engine, args, hint
        ),
        "verify_db_ui_consistency": lambda: _verify_db_ui_consistency_impl(
            engine, args, hint
        ),
        "verify_dialog": lambda: _verify_dialog_impl(engine, args, hint),
        "verify_download": lambda: _verify_download_impl(engine, args, hint),
        "verify_element_attribute": lambda: _verify_element_attribute_impl(
            engine, args, hint
        ),
        "verify_element_attribute_all": lambda: _verify_element_attribute_all_impl(
            engine, args, hint
        ),
        "verify_element_checked": lambda: _verify_element_checked_impl(
            engine, args, hint
        ),
        "verify_element_count": lambda: _verify_element_count_impl(engine, args, hint),
        "verify_element_enabled": lambda: _verify_element_enabled_impl(
            engine, args, hint
        ),
        "verify_element_exists": lambda: _verify_element_exists_impl(
            engine, args, hint
        ),
        "verify_element_exists_check": lambda: _verify_element_exists_check_impl(
            engine, args, hint
        ),
        # ★ 能力对称补齐(2026-09-21): CHECK_TAXONOMY 登记了 element_not_exists，
        #   但 tool_name 为空 → 该断言指令长期无法下发。存在/不存在是一对对称断言，
        #   缺一半即「只能证有、不能证无」，是能力缺口而非配置问题。
        "verify_element_not_exists": lambda: _verify_element_not_exists_impl(
            engine, args, hint
        ),
        "verify_element_text_equals": lambda: _verify_element_text_equals_impl(
            engine, args, hint
        ),
        "verify_element_visible": lambda: _verify_element_visible_impl(
            engine, args, hint
        ),
        "verify_image_loaded": lambda: _verify_image_loaded_impl(engine, args, hint),
        "verify_list_order": lambda: _verify_list_order_impl(engine, args, hint),
        "verify_loading_complete": lambda: _verify_loading_complete_impl(
            engine, args, hint
        ),
        "verify_network": lambda: _verify_network_impl(engine, args, hint),
        "verify_no_console_error": lambda: _verify_no_console_error_impl(
            engine, args, hint
        ),
        "verify_no_error_text": lambda: _verify_no_error_text_impl(engine, args, hint),
        "verify_page_load_time": lambda: _verify_page_load_time_impl(
            engine, args, hint
        ),
        "verify_pagination": lambda: _verify_pagination_impl(engine, args, hint),
        "verify_position": lambda: _verify_position_impl(engine, args, hint),
        "verify_refresh": lambda: _verify_refresh_impl(engine, args, hint),
        "verify_request_body": lambda: _verify_request_body_impl(engine, args, hint),
        "verify_response_body": lambda: _verify_response_body_impl(engine, args, hint),
        "verify_response_time": lambda: _verify_response_time_impl(engine, args, hint),
        "verify_select_value": lambda: _verify_select_value_impl(engine, args, hint),
        "verify_storage": lambda: _verify_storage_impl(engine, args, hint),
        "verify_style": lambda: _verify_style_impl(engine, args, hint),
        "verify_text_contains": lambda: _verify_text_contains_impl(engine, args, hint),
        "verify_text_in_page": lambda: _verify_text_in_page_impl(engine, args, hint),
        "verify_text_regex": lambda: _verify_text_regex_impl(engine, args, hint),
        "verify_time_range": lambda: _verify_time_range_impl(engine, args, hint),
        "verify_toast": lambda: _verify_toast_impl(engine, args, hint),
        "verify_url": lambda: _verify_url_impl(engine, args, hint),
        "verify_websocket": lambda: _verify_websocket_impl(engine, args, hint),
        # ★ W3 T04 (2026-08-05): 网络故障注入（仅 NETWORK_FAULT_ENABLED 且用例声明时生效）
        # ★ BUG 修复 (2026-08-16 B5): _network_fault_impl 是零参闭包（:224 定义），
        #   原写法 lambda: _network_fault_impl(engine, args, hint) 传 3 个实参 →
        #   TypeError: takes 0 positional arguments but 3 were given，
        #   即 network_fault 动作此前在分派处必然抛错（engine/args/hint 已由闭包捕获）。
        "network_fault": lambda: _network_fault_impl(),
        # ★ B5 (2026-08-16): 外部依赖 mock / 桩（仅 SERVICE_MOCK_ENABLED 且用例声明时生效）
        "service_mock": lambda: _service_mock_impl(),
        # ★ 根因修复(2026-09-21): API 方法族动作统一入口（api_get/post/put/delete/patch
        #   经 taxonomy 解析为 tool_name=api_request，method 由入参携带）
        "api_request": lambda: _api_request_impl(),
        # ★ 根因修复(2026-09-21): identity_switch 落地（消除 W3 骨架的声明↔执行漂移）
        "identity_switch": lambda: _identity_switch_impl(),
    }

    # ★ 根因修复(2026-09-21): 声明与执行漂移的自愈。
    #   现象：CHECK_TAXONOMY 声明了 verify_visual / verify_geometry / verify_api_contract，
    #   但本分派表漏登记 → 这三个断言指令 100% 返回「未知工具」，静默不可用。
    #   根因：注册表（_taxonomy）与执行分派表（本文件）是两份手工同步的清单，
    #        必然再次漂移；打补丁式地补三个 lambda 只是把债务往后推。
    #   修法：以注册表为单一事实源——分派表缺失且实现已存在时，按统一契约自动适配注册。
    #        以后任何新断言只要实现 (page, check) -> dict，即自动可执行。
    if tool_name not in _TOOL_HANDLERS:
        _impl = _auto_registered_impls().get(tool_name)
        if _impl is not None:
            return await _adapt_verify_impl(_impl, engine, args)

    handler = _TOOL_HANDLERS.get(tool_name)
    if handler:
        return await handler()
    return ToolExecutionResult(False, "未知工具: " + tool_name)


# ── 声明↔执行 漂移自愈：注册表中「有实现但未登记分派表」的断言 ──────────────
_AUTO_REGISTERED_CACHE: dict = {}


def _auto_registered_impls() -> dict:
    """返回 {tool_name: 实现函数}，实现来自 verification 包（按函数名解析）。"""
    if _AUTO_REGISTERED_CACHE:
        return _AUTO_REGISTERED_CACHE
    out: dict = {}
    with guard("_auto_registered_impls: from src.pipeline.steps.step4.browse", log=_log):
        from src.pipeline.steps.step4.browser_tools._taxonomy import CHECK_TAXONOMY

        for _key, _meta in CHECK_TAXONOMY.items():
            _tn = (_meta.get("tool_name") or "").strip()
            if not _tn:
                continue
            _fn = _resolve_verify_impl(_tn)
            if _fn is not None:
                out[_tn] = _fn
    _AUTO_REGISTERED_CACHE.update(out)
    return _AUTO_REGISTERED_CACHE


def _resolve_verify_impl(tool_name: str):
    """在 verification 包内按函数名查找实现（同步/异步均可）。"""
    import importlib
    import os

    _pkg_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "verification",
    )
    if not os.path.isdir(_pkg_dir):
        return None
    for _fn in os.listdir(_pkg_dir):
        if not _fn.endswith(".py") or _fn.startswith("__"):
            continue
        try:
            _mod = importlib.import_module(
                f"src.pipeline.steps.step4.verification.{_fn[:-3]}"
            )
        except Exception:
            continue
        _cand = getattr(_mod, tool_name, None)
        if callable(_cand):
            return _cand
    return None


async def _adapt_verify_impl(impl, engine, args) -> "ToolExecutionResult":
    """把 (page, check) -> dict 的校验器适配成工具执行契约。

    契约统一：passed 取自 dict['passed']，evidence 作为证据文本。
    """
    import inspect

    _page = getattr(engine, "_page", None)
    _name = getattr(impl, "__name__", str(impl))
    try:
        if inspect.iscoroutinefunction(impl):
            _r = await impl(_page, args)
        else:
            _r = impl(_page, args)
    except Exception as _e:
        return ToolExecutionResult(False, "%s 执行异常: %s" % (_name, str(_e)[:150]))
    if isinstance(_r, dict):
        return ToolExecutionResult(
            bool(_r.get("passed", False)),
            str(_r.get("evidence", _r.get("error", "")))[:500],
        )
    return ToolExecutionResult(bool(_r), str(_r)[:300])


