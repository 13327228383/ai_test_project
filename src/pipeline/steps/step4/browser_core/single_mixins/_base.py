# -*- coding: utf-8 -*-
"""单意图执行 — 基础框架 mixin。

职责：
- _execute_one_intent 主框架（前置检查、插件、会话感知、导航、数据工厂、步骤循环入口）
- 硬验证、步骤结果记录、日志、插件 after_step、进度更新、意图结束处理

AI 多轮循环和结构化动作分别委托到 _ai_loop 和 _structured mixin。
★ §4.3 单文件≤800行（2026-08-06）: step_action 分类助手拆到 _base_classify.py，
  Agent 探索后 checks 验证拆到 _agent_checks.py（_BrowserCoreAgentChecksMixin）。
"""

import asyncio
from src.core.guarded import guard
import logging
import time

from src.core.config_loader import config
from src.pipeline.utils.form_fill_policy import autofill_allowed
from src.pipeline.utils.pause_controller import pause_controller

from ._assertion_verdict import (
    declares_executable_assertion,
    has_executed_assertion_verdict,
    has_no_negative_verdict,
    has_not_applicable_assertion,
    has_only_engine_gap_verdict,
)

from ._base_classify import (
    _safe_int,
    _is_structured_action_step,
    _is_structured_assertion_step,
)
from .._worker_errors import get_worker_errors, get_worker_network
from ...playwright_engine.js_load_phase import is_case_attributable

_log = logging.getLogger(__name__)


def _target_auth_none() -> bool:
    """当前项目靶子是否声明 auth.type=='none'（无需登录）——统一读 config 单源判定。"""
    try:
        return bool(getattr(config, "target_auth_none", False))
    except Exception as _e:  # noqa: BLE001
        _log.debug("[SessionAware] target_auth_none 判定失败(按需登录处理): %s", str(_e)[:80])
        return False


try:
    from gui_app import log_step_ok, log_step_fail, update_monitor_dom
except Exception as _e:
    _log.debug("[_base] _safe_int 异常: %s", _e, exc_info=True)
    log_step_ok = lambda *a: print(
        "    [Step %d/%d] OK (%.1fs) — %s" % (a[0], a[1], a[2], a[3][:50])
    )
    log_step_fail = lambda *a: print(
        "    [Step %d/%d] FAIL (%.1fs) — %s" % (a[0], a[1], a[2], a[3][:50])
    )
    update_monitor_dom = lambda c, o: None


class _BrowserCoreSingleBaseMixin:
    """单意图执行基础框架。"""

    # ★ _resolve_step_hints_from_cache 已拆到 _structured.py（§4.3 单文件≤800行）

    async def _execute_one_intent(
        self, intent, step_num, total, ai, api_tracker, data_factory, progress_callback
    ):
        engine = self._engine
        # ★ v4.3.1: 防御 — intent 必须是 dict，否则 .get() 崩溃
        if not isinstance(intent, dict):
            _log.error(
                "[_execute_one_intent] intent 类型异常: %s (type=%s), value=%.200s",
                step_num,
                type(intent).__name__,
                str(intent)[:200],
            )
            return {
                "case_id": f"step{step_num}",
                "passed": False,
                "error": f"intent 类型错误: {type(intent).__name__}",
            }
        case_id = intent.get("case_id", f"step{step_num}")
        case_meta = intent.get("case_meta", {})
        # ★ C5 [未复核-需 GLM/DeepSeek 把关]: 并行路径用例级网络证据（task-local）
        _case_network_logs = []
        intent_title = case_meta.get("title", "")
        case_type = case_meta.get("case_type", "")
        risk_level = case_meta.get("risk_level", "")
        intent_desc = intent.get("case_description", "")
        # ★ 修复断链：extract_intent_schema 返回 (intent_schema, payload) 元组，
        #   原代码未解包直接当 dict 用 → 'tuple' object has no attribute 'get' 崩。
        #   _io.py:67 同函数已正确解包，此处对齐。
        # ★ 通用修复(2026-07-21): payload 含 checks/required_tracks/description 等，
        #   置信度计算需要这些字段（case_completeness/semantic_align 依赖），
        #   原变量名 _intent_payload 带下划线表示未使用，导致置信度拿不到数据。
        intent_schema, intent_payload = self._extract_intent_schema(intent)
        # 提交前必填补全的极性门控：判据由 Step3 落库前一次性判定并写
        # `form_fill_policy`（生产点 utils.form_fill_policy.declare_form_fill_policy），
        # 执行端只读该字段；字段缺失的历史行由同一判据函数回退求值。
        _case_allow_autofill = autofill_allowed({**intent, **(intent_payload or {})})
        steps = self._normalize_steps(self._steps_from_case(intent_schema, intent))
        meta_text = self._build_meta_text(case_meta)
        step_results = []
        all_passed = True
        intent_t0 = time.time()
        self._current_case_id = case_id

        # ★ 用例执行上下文（唯一写入点）：把「本用例期望被拒绝/不可达」这条语义
        #   一次性判定后写进 ContextVar，下游（错误页判定/404 语义）只读不推。
        #   生产函数唯一 = step3._identity_assign.case_expects_denial。
        #   不写 = 期望 403 的访问控制用例会被当"页面错误"重登+菜单回退后判假 FAIL。
        with guard("_execute_one_intent: set_case_expects_denial", log=_log):
            from src.core.case_context import set_case_expects_denial
            from src.pipeline.step3._identity_assign import case_expects_denial

            set_case_expects_denial(
                case_expects_denial({**intent, **(intent_payload or {})})
            )

        # ★ P0-4: 车道闸门 — 执行前检查 review_lane，red lane 阻断执行
        #   通用性: 跨系统/行业/页面 — 只依赖 review_lane 表，不依赖业务术语
        #   向后兼容: review_lane 无数据时放行，不阻断旧项目
        with guard("_execute_one_intent: from src.pipeline.utils.review_lane_", log=_log, level=logging.WARNING):
            from src.pipeline.utils.review_lane_store import check_lane_gate
            from src.users.audit import _active_version_id

            _proj = (
                case_meta.get("project", "")
                or getattr(config, "project_name", "")
                or ""
            )
            _vid = _active_version_id(_proj)
            _gate = check_lane_gate(case_id, _proj, _vid)
            if _gate.get("blocked"):
                _log.warning(
                    "[LaneGate] 阻断执行 case_id=%s lane=red reason=%s",
                    case_id,
                    _gate.get("reason", "")[:80],
                )
                return {
                    "case_id": case_id,
                    "passed": False,
                    "error": "车道闸门阻断: %s" % _gate.get("reason", "未通过审核"),
                    "failure_category": "LANE_BLOCKED",
                    "_lane_gate": _gate,
                }

        # ★ S45-008: 强制停止检查（在 action 执行前检查，减少长操作延迟）
        if self._force_stop_check and self._force_stop_check():
            if not self._stop_printed:
                _log.warning("[STOP] 收到停止信号，中止执行[%s]", case_id)
                self._stop_printed = True
            return {
                "case_id": case_id,
                "passed": False,
                "error": "用户强制停止",
                "_stopped": True,
            }

        # ★ [FIX] 用例开题前补暂停检查点
        #    serial.py 外层循环虽已检查，但开题阶段含 _check_preconditions、
        #    before_intent 插件、会话感知自动登录、数据工厂预处理等耗时操作
        #    若用户在外层循环通过后、此处之前点暂停，仍需在此响应
        await pause_controller.async_sleep_while_paused()

        _log.info("\n" + "=" * 60)
        _log.info("[Step %d/%d] 测试用例: %s", step_num, total, intent_title or case_id)
        _log.info("=" * 60)

        # ★ 通用修复(2026-07-27): 用例开始时清除瞬态提示缓存，防止跨用例污染
        #   根因(§10.9 5层追问):
        #     1. 现象: TC-REQ-003-D01/TC-REQ-001-D01 ERROR — no_error 检查发现瞬态错误
        #        'invalid.exe 不是一个 Zip 文件'
        #     2. 直接原因: _handle_no_error 从 page._transient_hints 读到前一个用例的错误
        #     3. 中间原因: _capture_transient_hints 合并 window.__e2e_transient_hints 到
        #        page._transient_hints，但未在用例间清除
        #     4. 深层原因: window.__e2e_transient_hints (MutationObserver) 跨页面导航累积，
        #        前一个页面的 toast 错误被带到下一个页面
        #     5. 根因: 瞬态提示缓存是"执行级"缓存（page 生命周期），但校验逻辑把它当
        #        "用例级"缓存使用，缺少用例边界清除
        #   通用性(§12.4 9维度):
        #     1. 跨系统: 所有 SPA 系统都可能存在跨页面 toast 污染
        #     2. 跨行业: 电商/SaaS/ERP/配方系统通用
        #     3. 跨页面: 不依赖页面类型
        #     4. 跨Tab: 不依赖 Tab 实现
        #     5. 数据完整性: 清除缓存不影响当前用例的 hint 捕获（observer 已注入）
        #     6. 根因溯源: 5层追问到缓存生命周期管理缺陷
        #     7. 覆盖性: 所有 _execute_one_intent 调用路径都受益
        #     8. 全局观: 不影响 Step1-Step5 其他步骤，仅影响 Step4 校验准确性
        #     9. 职责边界: Step4 在自己职责内修复缓存生命周期管理
        #   自我反驳三次:
        #     1) 只在 _handle_no_error 中过滤？→ 治标不治本，其他使用 _transient_hints
        #        的检查（_match_transient_hints）也会被污染
        #     2) 在 _capture_transient_hints 中过滤？→ 无法区分"前用例"和"当前用例"的 hint
        #     3) 在验证阶段清除？→ 太晚，验证已经读到污染数据
        #   取最优解: 用例开始时清除（最早边界），确保后续所有检查都只看到当前用例的 hint
        with guard("_execute_one_intent: if engine._page is not None: # ★ P0 ", log=_log):
            if engine._page is not None:
                # ★ P0 盲区补丁 GAP-01 [已复核-DeepSeek-V3 2026-08-16(含RISK缓解)] (2026-08-15):
                #   用例级清空 JS 运行时报错缓冲(engine._console_errors)，与瞬态提示
                #   缓存同一生命周期边界，避免跨用例污染。引擎在 playwright_engine/
                #   lifecycle.py 已注册 page.on("console")/page.on("pageerror") 监听并
                #   按 type=="error" / pageerror 规则缓存到此列表；此前从未把该缓存
                #   并入用例裁决 → 坏页面照样全绿(盲测 js_error_silent 8/8 全漏)。
                #   此处仅做"用例级重置"，监听器本身不动(零重构、零契约改动)。
                # ★ T1 (2026-08-16) [未复核-需 GLM/DeepSeek 把关]:
                #   并行路径清空当前 worker 自己的缓冲（contextvars task-local，
                #   同批 worker 互不可见），不碰共享 engine._console_errors；
                #   串行路径回落原语义（engine._console_errors = []）。
                with guard("_execute_one_intent: _wb = get_worker_errors()", log=_log):
                    _wb = get_worker_errors()
                    if _wb is not None:
                        _wb.clear()
                    else:
                        engine._console_errors = []
                    # ★ C5 [根因修复-用例边界清空/D-2026-09-04]: 用例开始时同步清空 console/network
                    #   证据缓冲，避免串行同一 engine 多用例跨用例累积 + 内存无界增长。
                    #   并行路径清空当前 worker 自己的缓冲（contextvars task-local，同批 worker
                    #   互不可见，不误清他人缓冲）；串行路径回落清空共享 engine._console_logs /
                    #   engine._network_logs（与上文 _console_errors 同生命周期边界范式）。
                    _wn = get_worker_network()
                    if _wn is not None:
                        _wn.clear()
                    else:
                        # 串行：清空 engine 级证据缓冲（存在则重置，缺失则安全创建）
                        setattr(engine, "_console_logs", [])
                        setattr(engine, "_network_logs", [])
                    # 原生弹窗捕获与上面同一边界清空：上一用例的弹窗会把本用例的
                    #   dialog_check 撑成假 PASS。
                    setattr(engine, "_captured_dialogs", [])
                # 清除 Python 侧缓存
                setattr(engine._page, "_transient_hints", [])
                # ★ 列表规模基线（用例级，唯一来源）：仅**重置**为 None —— 真正的采集在
                #   首个交互动作**之前**（`action_hooks.run_action_with_hooks`）进行，
                #   因为此刻页面还停在上一用例的页面上，采不到本用例的列表事实。
                setattr(engine._page, "_case_row_baseline", None)
                # 前置数据需求（Step3 一次性声明）：本用例只读该字段，声明/缺口/完成标记
                #   同一边界重置，避免跨用例污染。
                setattr(
                    engine._page,
                    "_case_data_precondition",
                    str(
                        (intent_payload or {}).get("data_precondition")
                        or intent.get("data_precondition")
                        or ""
                    ),
                )
                setattr(engine._page, "_case_data_precondition_done", False)
                setattr(engine._page, "_case_data_precondition_gap", "")
                # 清除浏览器侧 MutationObserver 累积
                await engine._page.evaluate(
                    "() => { window.__e2e_transient_hints = []; }"
                )
                _log.debug("[Transient] 用例开始，已清除瞬态提示缓存")

        # --- 前置检查 ---
        if not await self._check_preconditions(case_id, case_meta, steps):
            _log.error("[ERROR] 前置条件检查失败: %s", case_id)
            return {"case_id": case_id, "passed": False, "error": "前置条件检查失败"}

        # --- 插件：before_intent ---
        await self._plugin_manager.execute_hook(
            "before_intent",
            case_id=case_id,
            case_meta=case_meta,
            steps=steps,
            engine=engine,
            ai=ai,
        )

        # --- 智能会话感知 ---
        # ★ 通用修复(2026-07-20): 自动登录失败应阻断执行（"沉默即失败"红线）
        # 根因: 原仅 warning 放行，导致未登录态下执行所有步骤 → step_results 为空 →
        #       all(r["passed"] for r in []) = True（空真值）→ case_passed=True → 假PASS
        # 通用性: 登录是所有系统/行业/页面的前置条件，无登录态后续步骤无意义
        # 覆盖性: 适用于所有调用 _execute_one_intent 的用例（P01/N01/B01/降级用例等）
        _session_login_failed = False
        # ★ 根因修复(2026-09-25) RC-L: 匿名用例禁止自动登录。
        #   根因: 用例显式声明 identity=anonymous（如"未登录用户访问业务页面须重定向
        #     登录页"）时，per-case 切换已把凭据置空，但此处 `config.target_username
        #     or "admin"` 的空值兜底又把用户名还原成 admin，并以空密码尝试登录 →
        #     必失败 → 阻断执行 → 引擎自造 HARNESS_ERROR（实测 TC-REQ-021-E01）。
        #   矛盾性: 引擎先登录成功再去断言"未登录应被重定向"，验证手段与期望性质
        #     直接对立 —— 即使 SUT 完全正确也必然判失败。
        #   通用修复: 匿名是**显式声明的身份语义**（未登录态即被测条件），跳过自动
        #     登录，交由用例自身断言"被重定向/被拒绝"。
        #   通用性: 跨系统/行业/页面 —— 纯身份语义判定，零业务硬编码。
        _identity_anonymous = False
        with guard("_execute_one_intent: from src.pipeline.utils.identity_orc", log=_log):
            from src.pipeline.utils.identity_orchestrator import (
                current_identity_anonymous,
            )

            _identity_anonymous = current_identity_anonymous()
        if _identity_anonymous:
            _log.info(
                "[SessionAware] 用例身份显式声明为 anonymous → 跳过自动登录"
                "（未登录态即被测条件）: case=%s",
                case_id,
            )
        elif _target_auth_none():
            # ★ 2026-09-28 通用回归：靶子注册表 auth.type=none（如 smartshop Flask 版，
            #   无登录体系）时，Web Step4 会话感知仍逐路径探测登录 → /admin/ 等路径 404
            #   → 登录失败 BLOCKED（empirical 路径早已同语义跳过）。此处单源对齐：
            #   匹配到 target_cfg 且 auth.type=="none" 即认为无需登录，跳过自动登录。
            _log.info(
                "[SessionAware] 靶子无需登录 (auth.type=none) → 跳过自动登录: case=%s",
                case_id,
            )
            _login_ok = True
        elif getattr(config, "session_aware_login", True):
            with guard("_execute_one_intent: _cur_url = (engine._page.url or '').", log=_log, level=logging.WARNING):
                # ★ 2026-08-08 v2: 先检查是否已登录，避免重复登录
                #   根因: ai/login.py 的 _try_deterministic_login 已成功登录，但此处
                #   会话感知又去 /admin/login/ 找用户名输入框。已登录用户访问 /admin/login/
                #   被 Django 重定向到 /admin/，无 username 输入框 → wait_for_selector 超时
                #   → _login_ok=False → _session_login_failed=True → 阻断执行。
                _cur_url = (engine._page.url or "").lower() if engine._page else ""
                _has_pwd = False
                with guard("_execute_one_intent: _has_pwd = await engine._page.locato", log=_log):
                    _has_pwd = await engine._page.locator(
                        'input[type="password"]'
                    ).count() > 0
                if "/login" not in _cur_url and not _has_pwd:
                    _log.info(
                        "[SessionAware] 当前页面无需登录(URL=%s)，跳过登录",
                        _cur_url[:80],
                    )
                    _login_ok = True
                else:
                    _log.info("[SessionAware] 主动尝试登录(通用性修复: 当前页优先+稳健选择器+回退探测)...")
                    _target = (config.target_url or "").rstrip("/")
                    _username = config.target_username
                    _password = config.target_password or ""
                    _login_path = (getattr(config, 'target_login_path', '') or '').strip()
                    # ★ 根因修复(2026-09-21) [未复核-需 GLM/DeepSeek 把关]:
                    #   内联登录实现（~100 行闭包）抽取为 browser_core/session_login.py
                    #   单一实现，与 identity_switch 动作共用（消除重复轮子）。
                    #   语义与原内联版一致：当前页优先 → 配置路径/通用路径探测 → 填写验证。
                    from ..session_login import session_login

                    _login_ok, _login_detail = await session_login(
                        engine._page,
                        engine.goto,
                        _target,
                        _username,
                        _password,
                        login_path=_login_path,
                        username_sel=(getattr(config, 'login_username_selector', '') or ''),
                        password_sel=(getattr(config, 'login_password_selector', '') or ''),
                        submit_sel=(getattr(config, 'login_submit_selector', '') or ''),
                        origin="SessionAware",
                    )
                if _login_ok:
                    _log.info("[SessionAware] 主动登录成功")
                else:
                    _log.warning("[SessionAware] 直接登录失败，阻止执行（避免假PASS）")
                    _session_login_failed = True
                    step_results.append(
                        {
                            "step_idx": 0,
                            "description": "智能会话感知自动登录",
                            "action": "auto_login",
                            "passed": False,
                            "error": "自动登录失败：未登录态无法执行后续步骤",
                            "elapsed": 0.0,
                            "_hard_verified": False,
                        }
                    )
                    all_passed = False

        # ★  根因修复 [未复核-需 GLM/DeepSeek 把关]: 恢复「DOM 解析 + 导航树创建」能力
        #   根因(5层why):
        #     L1: Step3 WARNING "nav_graph 无导航数据 → fallback 用例无法定位真实URL"；
        #         Step4 WARNING "项目无 nav_graph_nodes → 策略1b 缓存恒失效"。
        #     L2: 全仓 write_nav_graph 调用点仅两处: _nav_map_builder(需 Confluence/Figma 数据源)、
        #         nav_seed(按 target_cfg 显式声明的专用 seed)——通用项目两路都不触发。
        #     L3: v5.16(2026-07-31) 因"每例承受 120s 预检成本"删除了预检调用，
        #         但未补任何一次性替代 → _preflight_build_nav_graph 成死代码，
        #         「目标系统 DOM 解析 + 导航树创建」能力整体归零。
        #   修复: 浏览器会话登录成功后做**一次性**预检——_preflight_build_nav_graph 自带
        #         DB 门控(nav_graph_nodes>10 即跳过，仅一次 COUNT 查询/例)，
        #         真实探索只发生一次(~120s)，非每例成本；失败仅 WARNING，不阻断用例执行。
        #   通用性: 侧边栏展开+菜单遍历+DOM 快照是 Web 应用通用模式；幂等由 DB 计数保证。
        # ★ 根因修复(2026-09-25) RC-L 同源: 匿名会话不得驱动导航树预检。
        #   根因: 导航清单是**已登录态的菜单/页面盘点**，匿名会话看不到任何受限菜单 →
        #     若首个执行用例恰为 anonymous，预检会以未登录视角落库一份残缺导航图，
        #     污染后续所有用例的模块→页面解析（RC-F2/RC-E 均依赖该数据）。
        #   通用修复: 匿名身份下跳过预检（未登录视角不构成导航事实）。
        if not _session_login_failed and not _identity_anonymous and engine._page:
            with guard("_execute_one_intent: from ._exploratory import _preflight", log=_log, level=logging.WARNING):
                from ._exploratory import _preflight_build_nav_graph as _pfb

                _pf_project = (
                    (case_meta.get("project") if isinstance(case_meta, dict) else "")
                    or getattr(config, "project_name", "")
                    or ""
                )
                # ★ 外键修正(2026-09-23): nav_graph_nodes.fk(project,version_label)→project_versions，
                #   version_label 传空串则 INSERT IGNORE 全部静默丢弃（实测 3 页 0 条）。
                #   与 _agent_executor 同源取激活版本标签。
                _pf_version = ""
                with guard("_execute_one_intent: from src.users.project_auth import g", log=_log):
                    from src.users.project_auth import get_active_version_label as _pf_gavl

                    _pf_version = _pf_gavl(_pf_project) or ""
                _pf_n = await _pfb(engine._page, _pf_project, _pf_version)
                if _pf_n > 0:
                    _log.info(
                        "[Preflight] 导航树构建/补充完成: 新发现 %d 个页面 (实时 DOM 侦察)",
                        _pf_n,
                    )

        # --- 数据工厂预处理 ---
        if data_factory and steps:
            with guard("_execute_one_intent: preprocessed = data_factory.preproce", log=_log, level=logging.WARNING):
                preprocessed = data_factory.preprocess_steps(steps, case_meta)
                if preprocessed:
                    steps = preprocessed
                    _log.info("[DataFactory] 步骤预处理完成")

        # ★ P0-refactor(2026-07-27): 所有意图驱动用例统一走 AgentExplorer 实时探索
        #   架构决策(§16.2): 取消"先爬全再测试"模式，Agent在浏览器中实时感知→决策→执行
        #   所有含 _explore_task 的用例（Step3 统一生成）走此路径；
        #   无 _explore_task 的遗留用例走下方标准 step loop（向后兼容）。
        #   ★ §4.3 单文件≤800行（2026-08-06）: Agent 路径拆到 _agent_checks.py
        #     （_BrowserCoreAgentChecksMixin._try_agent_explorer_path）
        _agent_result = await self._try_agent_explorer_path(
            engine,
            intent,
            case_meta,
            case_id,
            step_results,
            all_passed,
            intent_t0,
            _session_login_failed,
            intent_desc,
        )
        if _agent_result is not None:
            return _agent_result

        # --- 执行各步骤 ---
        for step_idx, step in enumerate(steps):
            # ★ 通用修复(2026-07-20): 登录失败时跳过步骤执行（无登录态后续步骤无意义）
            if _session_login_failed:
                _log.info("[Step %d] 跳过执行：登录失败已阻断", step_idx + 1)
                break
            step_num_display = step_idx + 1
            step_desc = step.get("description", "")
            step_expected = step.get("expected", "")
            step_action = step.get("action", "")
            step_t0 = time.time()
            step_passed = True
            step_error = ""
            pre_dom_count = 0

            # ★ 指标: 每个 step 记录一次 action_attempt（step 级，不是 case 级）
            #   覆盖所有执行路径（structured / capability / AI loop），避免某些路径漏采集
            with guard("_execute_one_intent: from src.backend.state import _threa", log=_log):
                from src.backend.state import _thread_run_id
                from src.extensions.metrics_collector import metrics_collector

                # ★ 根因修复(2026-09-20): 此处原本重复 import config，Python 作用域规则
                #   使 config 成为本函数局部变量，导致函数前段(会话感知登录检查)引用
                #   config 时抛 UnboundLocalError → 所有走 single_mixins 路径的用例中断。
                #   模块顶部(第17行)已有等价 import，直接复用，不再函数内重复绑定。
                _rid = getattr(_thread_run_id, "value", "") or ""
                _proj = getattr(config, "project_name", "") or ""
                # action_type 取 step 里的 type / action / action_type 三选一
                _atype = (
                    step.get("type", "")
                    or step_action
                    or step.get("action_type", "")
                    or "unknown"
                )
                if _rid and _proj and _atype:
                    metrics_collector.record_action_attempt(
                        project=_proj,
                        run_id=_rid,
                        action_type=_atype,
                    )

            _log.info(
                "\n  [Sub-step %d.%d] %s", step_num, step_num_display, step_desc[:60]
            )

            # --- 暂停检查 ---
            # ★ [FIX] 原同步 check_pause() 会阻塞整个事件循环，并行模式下冻结所有 worker
            #    改用异步版本，挂起当前协程而不阻塞事件循环
            await pause_controller.async_sleep_while_paused()

            # --- 页面有效性检查 ---
            if not await self._ensure_page_valid(engine, ai):
                step_passed = False
                step_error = "页面失效"
                all_passed = False
                break

            # --- DOM 计数 ---
            try:
                pre_dom_count = await engine._page.locator("*").count()
            except Exception as _e:
                _log.debug("[_base] 非关键异常: %s", _e, exc_info=True)
                pre_dom_count = 0

            # --- 插件：before_step ---
            await self._plugin_manager.execute_hook(
                "before_step",
                case_id=case_id,
                step_idx=step_idx,
                step=step,
                engine=engine,
                ai=ai,
            )

            # --- 确定性能力路径（0-token，匹配到标准能力时先走确定性执行）---
            _cap_done = False
            _capability_method = step.get("_capability_method", "")
            if _capability_method:
                _capability_goal = step.get("_capability_goal", step_desc)
                try:
                    _cap_fn = getattr(ai, _capability_method, None)
                    if _cap_fn:
                        _log.info(
                            "    [Capability] 尝试确定性路径: %s → %s",
                            _capability_method,
                            _capability_goal[:60],
                        )
                        _cap_ok = await _cap_fn(_capability_goal)
                        if _cap_ok:
                            step_passed = True
                            step_error = ""
                            _cap_done = True
                            _log.info("    [Capability] ✅ 确定性路径成功，跳过 AI")
                        else:
                            _log.warning(
                                "    [Capability] 确定性路径失败，跳过此步，回退到结构化动作执行"
                            )
                            _cap_done = True  # 跳过 cap_step，让循环进入 step_entry（走 ActionDispatcher）
                except Exception as e:
                    _log.warning(
                        "    [Capability] 异常: %s，跳过此步，回退到结构化动作执行",
                        str(e)[:80],
                    )
                    _cap_done = True

            # --- 结构化动作执行（含策略1/3的零AI执行）---
            # ★ f3: step_action 分类改为 DB 驱动（action_library_store 的非断言分类），
            #   不再硬编码 ("click","fill","select_option","navigate","upload","execute")。
            #   "execute" 是元动作（执行 step["actions"] 列表），DB 失败时按 step 结构兜底。
            # ★ Fix53 (2026-07-09): 结构化动作执行失败后 fallback 到 AI 多轮循环
            #   契约: AGENTS.md §11.5.3 —— 查表优先、AI 兜底。规则能覆盖的走规则，覆盖不了的 AI 接手。
            _structured_ok = False
            _structured_all_failed = False
            _structured_fail_reason = ""
            if not _cap_done and _is_structured_action_step(step_action, step):
                with guard("_execute_one_intent: _page_explorer = getattr(self, '_pag", log=_log, level=logging.WARNING):
                    # ★ Phase 1 (2026-07-11): 用页面探索器缓存解析 target_hint
                    #   登录后已预缓存所有页面 DOM，此处用模糊匹配找实际元素
                    _page_explorer = getattr(self, "_page_explorer", None)
                    if _page_explorer and _page_explorer._explore_count > 0:
                        _current_url = engine._page.url if engine._page else ""
                        self._resolve_step_hints_from_cache(
                            step, _page_explorer, _current_url
                        )

                    # ★ P2-10 接入(2026-07-25): AgentExplorer replay — op_cache_v2 命中重放
                    #   命中 → 替换 step["actions"] 走原执行路径；未命中 → 走原路径（降级）
                    #   职责边界：执行优化（不跳过 checks 断言），§12.1 允许
                    from src.pipeline.steps.step4.browser_core.single_mixins._op_cache_replayer import (
                        replay_attempt as _replay_attempt,
                    )

                    _rep_hit, _ = _replay_attempt(
                        getattr(self, "_current_case_id", "") or "",
                        case_id,
                        case_meta,
                        intent,
                        step,
                        (engine._page.url or "") if engine._page else "",
                        getattr(config, "project_name", "") or "",
                    )
                    if _rep_hit:
                        _log.info(
                            "    [Structured] ★ P2-10 replay 命中，重放缓存动作序列"
                        )
                        # ★ F3 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：
                        #   结构化路径 replay 命中标记到 intent（供标准路径断言语义层读取），
                        #   与 Agent 路径 intent["op_cache_hits"]>0 双路径统一。
                        intent["_replay_hit"] = True

                    step["_allow_autofill"] = _case_allow_autofill
                    action_result = await self._execute_structured_actions(
                        step, step_desc
                    )
                    # ★ Fix (2026-07-10): 返回格式从 bool 改为 dict
                    if isinstance(action_result, dict):
                        _structured_ok = action_result.get("success", False)
                        _structured_all_failed = action_result.get("all_failed", False)
                        # ★ v19.2 通用修复(2026-07-23): 业务错误不进入 AI loop
                        #   根因: upload_file "系统拒绝上传"是业务错误，AI loop 无法修复
                        #         （AI 不知道系统期望什么文件格式），循环到超时浪费资源
                        #   通用性: 适用于所有 action 的业务错误（系统拒绝/API错误/权限不足）
                        #   覆盖性: 影响所有 structured action 失败后的 AI fallback 决策
                        #   职责边界: Step4 执行层内（判定错误类型是执行的一部分）
                        _structured_non_recoverable = action_result.get(
                            "non_recoverable", False
                        )
                        if _structured_all_failed:
                            _errors = action_result.get("errors", [])
                            _structured_fail_reason = (
                                "所有交互动作执行失败: " + "; ".join(_errors[:3])
                            )
                    elif action_result:  # 兼容旧 bool 返回值
                        _structured_ok = True
                    if _structured_ok:
                        _log.info("    [Structured] ✅ 结构化动作执行成功，跳过 AI")
                        # ★ P2-10 接入(2026-07-25): AgentExplorer record — 成功动作序列写入 op_cache_v2
                        #   拆出到 _op_cache_recorder.py（§4.3）；职责：数据收集，§12.1 允许
                        from src.pipeline.steps.step4.browser_core.single_mixins._op_cache_recorder import (
                            record_success_actions as _record_success,
                        )

                        _record_success(
                            getattr(self, "_current_case_id", "") or "",
                            case_id,
                            case_meta,
                            intent,
                            step,
                            (engine._page.url or "") if engine._page else "",
                            getattr(config, "project_name", "") or "",
                        )
                    elif _structured_non_recoverable:
                        _log.warning(
                            "    [Structured] v19.2: ⚠ 业务错误(不可恢复)，跳过 AI loop: %s",
                            _structured_fail_reason,
                        )
                    elif _structured_all_failed:
                        _log.warning(
                            "    [Structured] ⚡ 所有交互动作失败，回退到 AI loop: %s",
                            _structured_fail_reason,
                        )
                    else:
                        _log.warning(
                            "    [Structured] 结构化动作执行失败，回退到 AI 多轮循环"
                        )

            # --- 结构化断言 ---
            # ★ f3: step_action 分类改为 DB 驱动（action_library_store 的断言分类），
            #   不再硬编码 ("assert","verify")。"assert" 是元动作（执行 step["assertions"]）。
            # ★ Fix53: 结构化断言失败后也 fallback 到 AI
            _structured_assert_ok = False
            if (
                not _cap_done
                and not _structured_ok
                and _is_structured_assertion_step(step_action, step)
            ):
                with guard("_execute_one_intent: assert_result = await self._execute_", log=_log, level=logging.WARNING):
                    assert_result = await self._execute_structured_assertions(
                        step, step_desc
                    )
                    if assert_result:
                        _structured_assert_ok = True
                        _log.info("    [Structured] ✅ 结构化断言成功，跳过 AI")
                    else:
                        _log.warning(
                            "    [Structured] 结构化断言失败，回退到 AI 多轮循环"
                        )

            # --- AI 多轮循环 ---
            # ★ Fix53: 结构化执行失败时也进入 AI 多轮循环（fallback）
            # ★ 所有交互动作失败时，将错误信息注入 AI prompt 辅助决策，不跳过 AI loop
            # ★ v19.2 通用修复(2026-07-23): 业务错误(不可恢复)不进入 AI loop
            #   根因: upload_file "系统拒绝上传"是业务错误，AI loop 无法修复
            #         （AI 不知道系统期望什么文件格式），循环到超时浪费资源
            #   通用性: 适用于所有 action 的业务错误（系统拒绝/API错误/权限不足）
            #   覆盖性: 影响所有 structured action 失败后的 AI fallback 决策
            #   职责边界: Step4 执行层内（判定错误类型是执行的一部分）
            if _structured_all_failed and not _structured_non_recoverable:
                _log.warning(
                    "    [Structured] ⚡ 所有交互动作失败，进入 AI loop 兜底: %s",
                    _structured_fail_reason,
                )
                # 将结构化失败原因注入 step_desc，供 AI 参考
                step_desc = (
                    (step_desc or "")
                    + "\n【结构化执行失败原因】"
                    + _structured_fail_reason
                )
            if (
                not _cap_done
                and not _structured_ok
                and not _structured_assert_ok
                and not _structured_non_recoverable
            ):
                step_passed, step_error, all_passed = await self._run_ai_round_loop(
                    step=step,
                    step_desc=step_desc,
                    step_expected=step_expected,
                    case_meta=case_meta,
                    meta_text=meta_text,
                    engine=engine,
                    ai=ai,
                    step_passed=step_passed,
                    step_error=step_error,
                    all_passed=all_passed,
                    structured_all_failed=_structured_all_failed,
                )

                # ★ v10: 标准 step loop 路径的 cache 计数写回 intent 字典
                _ai_cache_hits = getattr(self, '_ai_loop_cache_hits', 0)
                _ai_cache_misses = getattr(self, '_ai_loop_cache_misses', 0)
                if _ai_cache_hits > 0:
                    intent["op_cache_hits"] = intent.get("op_cache_hits", 0) + _ai_cache_hits
                    _log.info("[Base] ★ v10: 标准step loop cache hits=%d → intent.op_cache_hits=%d",
                               _ai_cache_hits, intent["op_cache_hits"])
                if _ai_cache_misses > 0:
                    intent["op_cache_misses"] = intent.get("op_cache_misses", 0) + _ai_cache_misses

            # --- 硬验证 + 三轨验证 ---
            # ★ 三轨挂载：用 _verify_recipe_intent_result（硬校验 + ui/api/db 三轨客观校验）
            #   替代原 _hard_verify_step（仅 5 轨硬校验）。_hard_verify_step 保留供
            #   execute_structured_assertions 使用。返回 (bool, reason)。
            # ★ v4.3.1(2026-07-02): 能力路径成功时跳过验证，由后续 step_entry(含结构化校验) 负责。
            #   原逻辑对 cap_step 也调 verify_recipe_intent_result → 无结构化校验 → 回退
            #   hard_verify → 失败 → 中止执行 → 永远到不了有 checks 的 step_entry。
            _hard_verified = False
            _step_verify_result = None  # ★ v25 根因Z5: 保存 verify_result 三轨证据
            # ★ 判定与动作解耦（架构约束「AI 前移到生成侧、运行期零 AI」）：
            #   结构化断言是**权威判定**，动作是否成功不得阻断断言求值 —— 否则断言
            #   永不执行 → 用例被判"无有效断言"SKIP，需求主张无人验证（假无结论）。
            #   动作缺口作为证据保留在 step_error 中，主张是否成立由断言裁决。
            _structured_checks_present = any(
                step.get(k) for k in ("ui_checks", "api_checks", "db_checks")
            )
            if not _cap_done and step_expected and (
                step_passed or _structured_checks_present
            ):
                with guard("_execute_one_intent: verified, reason, _step_verify_resul", log=_log, level=logging.WARNING):
                    verified, reason, _step_verify_result = (
                        await self._verify_recipe_intent_result(
                            step=step,
                            step_desc=step_desc,
                            step_expected=step_expected,
                            intent_schema=intent_schema,
                            pre_dom_count=pre_dom_count,
                            api_tracker=api_tracker,
                            step_t0=step_t0,
                        )
                    )
                    if verified:
                        _hard_verified = True
                        # 断言成立 = 用例主张成立 → 本步通过（动作缺口已由断言证据覆盖）
                        step_passed = True
                        step_error = ""
                        all_passed = all(
                            bool(r.get("passed")) for r in step_results
                        )
                    else:
                        step_passed = False
                        step_error = "验证失败: %s" % (reason or "硬校验/三轨未通过")
                        all_passed = False

            # --- 记录步骤结果 ---
            step_elapsed = time.time() - step_t0
            step_result = {
                "step_idx": step_idx,
                "description": step_desc,
                "action": step_action or step_desc,
                "passed": step_passed,
                "error": step_error,
                "elapsed": round(step_elapsed, 2),
                "_hard_verified": _hard_verified,
                # ★ v25 根因Z5(2026-07-21): 保存 verify_result 三轨证据
                #   原 bug: verify_result 只返回 (bool, reason)，三轨 evidence 被丢弃，
                #          导致 record_execution 写 executions 表时 expected/actual/details 全空。
                #   修复: 把 verify_result 挂到 step_result，record_execution 时提取三轨 evidence。
                #   根因链路(5层):
                #     L1 表层: executions expected/actual/details 全空但 status=PASS（假PASS风险）
                #     L2 中层: _base.py 只在 check 失败时提取 evidence，PASS 时不提取
                #     L3 根因层: verify_recipe_intent_result 只返回 (bool, reason)，verify_result 丢弃
                #     L4 设计层: step_result 只记动作结果，不记 check 证据（执行与验证数据流断裂）
                #     L5 最深根因: verify_recipe_intent_result 返回值类型太窄，无法承载 check 证据
                #   通用性: 适用于所有走 verify_recipe_intent_result 的用例（跨系统/跨行业/跨页面）
                #   覆盖性: 所有走 _execute_one_intent 的用例都受益
                #   全局观: 修复后 Step5 报告有 evidence 可分析，假PASS检测有依据
                #   职责边界: Step4 内部数据流修复，不越界
                #   自我反驳:
                #     1) 扩展返回值会破坏调用方吗？→ verify_recipe_intent_result 只在 _base.py 一处调用，安全
                #     2) 有更通用方案吗？→ 统一所有执行器 evidence 写入路径（对齐 api_only_executor）
                #     3) 有更根本方案吗？→ 把 verify_result 挂 step_result 是最根本的数据流修复
                #   合规: AGENTS.md §10.6 禁止假PASS + §10.8 禁止治标不治本 + §10.9 根因核查流程
                "_verify_result": _step_verify_result,
            }
            step_results.append(step_result)

            # --- 日志输出 ---
            if step_passed:
                log_step_ok(step_num, step_num_display, step_elapsed, step_desc)
            else:
                log_step_fail(
                    step_num,
                    step_num_display,
                    step_elapsed,
                    f"{step_desc} | {step_error}",
                )
                # 保存失败截图
                with guard("_execute_one_intent: await self._save_fail_screenshot(cas", log=_log, level=logging.WARNING):
                    await self._save_fail_screenshot(case_id, step_num_display)

            # --- 插件：after_step ---
            # ★ 修复参数对齐：原仅传 step_idx，visual_baseline.on_after_step 读 step_index
            #   → step_index 永远为 0（默认值），导致 baseline_key 步骤序号错位。
            #   现补充 step_index/result/project，与 VisualBaselinePlugin 预期一致。
            await self._plugin_manager.execute_hook(
                "after_step",
                case_id=case_id,
                step_idx=step_idx,
                step_index=step_idx,
                step=step,
                result=step_passed,
                step_result=step_result,
                engine=engine,
                project=getattr(config, "project_name", "") or "default",
                ai=ai,
            )

            # --- 更新进度 ---
            if progress_callback:
                with guard("_execute_one_intent: progress_callback( 4, '智能执行验证', step", log=_log, level=logging.WARNING):
                    # ★ 参数对齐 _update_progress(run_id, step_num, step_label, step_done, step_total, from_step, to_step)
                    #   step_num=4 (固定 Step 4), step_label="智能执行验证"
                    #   step_done=step_num (当前用例编号), step_total=total (用例总数)
                    #   from_step=step_num, to_step=total (语义: 当前进度/总进度)
                    #   原 bug: 第 3 参数传 case_id(字符串), 第 4 参数传 step_passed(bool),
                    #   导致 _run_progress.step_done/step_total 被污染为非数字值。
                    progress_callback(
                        4, "智能执行验证", step_num, total, step_num, total
                    )

            # --- 如果步骤失败，是否继续 ---
            if not step_passed and not getattr(
                config, "continue_on_step_failure", False
            ):
                _log.info("  [Sub-step] 步骤失败且未配置继续，中止当前意图")
                break

        # --- ★ SecurityScanner 集成: 扫描 API track 采集的 HTTP 响应 ---
        #  在步骤循环结束后、置信度计算前执行安全扫描
        #  通用性: 跨系统/行业/页面 — 所有 HTTP 响应都应进行安全扫描
        #  覆盖性: 所有走 _execute_one_intent 的用例都受益
        security_findings: list[dict] = []
        with guard("_execute_one_intent: from src.extensions.security_scanner", log=_log, level=logging.WARNING):
            from src.extensions.security_scanner import SecurityScanner

            _scanner = SecurityScanner()

            # 1. 扫描 API tracker 采集的 HTTP 调用
            if api_tracker:
                _api_calls = api_tracker.get_calls_since(intent_t0)
                if _api_calls:
                    _scanner.scan_api_calls(_api_calls)
                    _log.info(
                        "[SecurityScanner] 扫描 %d 个 API 调用, 发现 %d 个安全问题",
                        len(_api_calls),
                        len(_scanner.findings),
                    )

            # 2. 扫描当前页面 DOM（补充 API 未覆盖的渲染层 XSS/CSRF）
            if engine._page is not None:
                with guard("_execute_one_intent: _page_content = await engine._page.c", log=_log):
                    _page_content = await engine._page.content()
                    _page_url = engine._page.url or ""
                    _scanner.scan_response(
                        body=_page_content,
                        content_type="text/html",
                        url=_page_url,
                        status_code=200,
                        headers=None,  # 页面内容无 headers
                    )

            security_findings = _scanner.to_dict_list()
            if security_findings:
                _log.warning(
                    "[SecurityScanner] case_id=%s 发现 %d 个安全问题: %s",
                    case_id,
                    len(security_findings),
                    ", ".join(
                        f"{f['severity']}:{f['category']}" for f in security_findings
                    ),
                )

        # --- 意图执行结束 ---
        intent_elapsed = time.time() - intent_t0
        # ★ 通用修复(2026-07-20): case_passed 加入 hard_verified 门槛，对齐 verdict 口径
        # 根因: 原 case_passed 仅基于 step_results.passed，与 browser_evidence.py 行125-126 的
        #       verdict["passed"] = passed AND hard_verified 口径不一致 → 假PASS
        # 通用性: PASS 必须同时满足"动作执行成功"(passed) AND "硬证据验证"(hard_verified)，
        #         此契约适用于所有系统/行业/页面/用例类型
        # 覆盖性: 所有走 _execute_one_intent 的用例（P01/N01/B01/降级用例）都受益
        _hard_verified_any = any(
            isinstance(r, dict) and r.get("_hard_verified") for r in step_results
        )
        case_passed = (
            all_passed and all(r["passed"] for r in step_results) and _hard_verified_any
        )

        # ★ P0 盲区补丁 GAP-01 [已复核-DeepSeek-V3 2026-08-16(含RISK缓解)] (2026-08-15):
        #   消费用例级 JS 运行时报错缓冲(engine._console_errors)。引擎在 lifecycle.py
        #   注册的 console/pageerror 监听已把 (a) type=="error" 的 console 消息、
        #   (b) 所有未捕获 pageerror 缓存进该列表；但此前从未并入用例裁决，
        #   导致"页面 JS 抛错但断言全绿"的假阴性(盲测 js_error_silent 类 8/8 0 检出)。
        #   最小增量修复(不改监听器/契约): 用例结束时若缓冲非空且当前判定为通过，
        #   则翻转为 FAIL，并追加一条断言失败项进入 evidence/status 判定链。
        with guard("_execute_one_intent: _wb = get_worker_errors()", log=_log):
            # ★ T1 (2026-08-16) [未复核-需 GLM/DeepSeek 把关]:
            #   并行路径只读当前 worker 自己的缓冲（不读共享缓冲，避免同批
            #   脏用例污染干净用例）；串行路径回落 engine._console_errors。
            _wb = get_worker_errors()
            if _wb is not None:
                _js_errors = list(_wb or [])
            else:
                _js_errors = list(getattr(engine, "_console_errors", []) or [])
            # ★ C5 [根因修复-串行回落/D-2026-09-04]: 并行路径取 worker 级网络缓冲
            #   （task-local 与 errors 同隔离范式），串行路径回落读取共享
            #   engine._network_logs（此前实现只取 worker 缓冲，串行 get_worker_network()
            #   返回 None → 恒为空，与注释声称的回落不符，属半成品断链）。
            _wn_snap = get_worker_network()
            if _wn_snap is not None:
                _case_network_logs = list(_wn_snap or [])
            else:
                _case_network_logs = list(
                    getattr(engine, "_network_logs", []) or []
                )
            # ★ GAP-01 RISK 缓解(2026-08-16): 第三方脚本噪音过滤逃生口。
            #   环境变量 GAP01_IGNORE_JS_PATTERNS(逗号分隔子串)命中的报错不计入 FAIL，
            #   用于放行广告/统计等第三方脚本的已知噪音(opt-in，默认不过滤→行为不变)。
            try:
                import os as _os

                _ig = [
                    p.strip().lower()
                    for p in (_os.environ.get("GAP01_IGNORE_JS_PATTERNS", "") or "").split(",")
                    if p.strip()
                ]
            except Exception:
                _ig = []
            if _ig and _js_errors:
                _kept = []
                for _e in _js_errors:
                    _txt = ("%s %s" % (_e.get("type", ""), _e.get("text", "") or "")).lower()
                    if not any(_p in _txt for _p in _ig):
                        _kept.append(_e)
                _js_errors = _kept
            # ★ 语义修正: "资源加载失败(Failed to load resource)"是**网络层**事实，
            #   不是 JS 运行时报错 —— 尤其访问控制用例主动驱动受限操作时，浏览器必然
            #   对 401/403 响应打 console.error，若计为 JS 缺陷会与同一用例
            #   access_control_check:✓(被拒绝) 自相矛盾（实测 TC-REQ-017-E01 假 FAIL）。
            #   真正的 JS 缺陷是 pageerror / 非资源加载类的 console.error。网络层事实
            #   由 network_logs 轨单独承载，此处不再越权计入。
            _js_errors = [
                _e for _e in _js_errors
                if "failed to load resource" not in (
                    "%s %s" % (_e.get("type", ""), _e.get("text", "") or "")
                ).lower()
            ]
            # ★ 根因修复(2026-10-28) 装载期报错不归因到用例：
            #   根因(3 层追问)：① 表层——访问首页的用例成批 FAIL，证据都是同一组
            #   "Executing inline event handler violates ... Content Security Policy"；
            #   ② 中层——这组报错在**文档装载期**就产生（同一页面对任何用例都一样），
            #   却被用例结束时的一次性缓冲快照全部计入；③ 根因——GAP-01 把
            #   "页面级存量事实"与"用例动作引发的增量事实"混为一谈，而不同浏览器
            #   实现对同一份页面缺陷的上报时机不同（有的只在真正执行内联处理器时
            #   报 1 条，有的在解析完成时对页面上每个内联处理器各报一条），于是
            #   一份页面缺陷被放大成 N 份用例假 Bug（实测 5 条 → 60+ 例）。
            #   判据单点收口 `js_load_phase.is_case_attributable`（生产者已在
            #   lifecycle/_worker_errors 打标），不按错误文案硬过滤、不设环境逃生口。
            #   通用性：装载期由 W3C 导航/load 事件定义，跨浏览器/系统/行业成立。
            _page_level_js = [e for e in _js_errors if not is_case_attributable(e)]
            _js_errors = [e for e in _js_errors if is_case_attributable(e)]
            if _page_level_js:
                _log.info(
                    "[GAP-01] 用例 %s 页面装载期存在 %d 条 JS 报错(页面级事实，不计入用例): %s",
                    case_id,
                    len(_page_level_js),
                    "; ".join(
                        "[%s] %s"
                        % (e.get("type", "?"), (e.get("text") or "")[:60])
                        for e in _page_level_js[:3]
                    ),
                )
            if _js_errors and case_passed:
                _js_err_summary = "; ".join(
                    "[%s] %s"
                    % (e.get("type", "?"), (e.get("text") or "")[:80])
                    for e in _js_errors[:5]
                )
                _log.warning(
                    "[GAP-01] 用例 %s 执行期间捕获 %d 条 JS 运行时报错 → 判 FAIL: %s",
                    case_id,
                    len(_js_errors),
                    _js_err_summary,
                )
                case_passed = False
                all_passed = False
                _js_evidence = (
                    "用例执行期间新增 %d 条 JS 报错(console.error/pageerror): %s"
                    % (len(_js_errors), _js_err_summary)
                )
                step_results.append(
                    {
                        "step_idx": -1,
                        "description": "页面 JS 运行时报错检测(GAP-01)",
                        "action": "js_runtime_error_check",
                        "passed": False,
                        "error": _js_evidence,
                        "elapsed": 0.0,
                        "_hard_verified": False,
                        # ★ 结构化归因通道：JS 运行时报错是引擎**已确证的产品行为证据**，
                        #   必须与其它断言同形进入判定链（`_assertion_verdict` 只读此结构）。
                        #   只留步骤级自由文本时不含 ✗/失败 标记 → 被判"无违反证据" →
                        #   `classify_failure` 因无策略执行记录归 HARNESS_ERROR(ERROR)，
                        #   把产品缺陷写成引擎错误（实测 39 例 CSP 内联事件处理器报错）。
                        "_verify_result": {
                            "checks": [
                                {
                                    "type": "js_runtime_error_check",
                                    "passed": False,
                                    "status": "FAIL",
                                    "evidence": _js_evidence,
                                }
                            ]
                        },
                    }
                )

        # --- 记录证据 ---
        with guard("_execute_one_intent: await self._record_intent_evidence( ", log=_log, level=logging.WARNING):
            await self._record_intent_evidence(
                case_id, case_meta, case_passed, step_results
            )

        # --- 记录执行到 executions 表（修复：浏览器路径此前不写 executions） ---
        project = getattr(config, "project_name", "") or ""
        # ★ 用例自洽契约（单一事实源 `utils/case_self_consistency`）：
        #   用例自述"先执行一次操作、再观察结果"（scenario_type=error_path/boundary
        #   或含观察型断言），但其动作里只有 navigate/screenshot、无任何状态变更动作
        #   ⇒ 断言前置未满足。此时**两个方向的判定都不成立**：记为 PASS 是假绿
        #   （静态页碰巧满足通用断言），记为 FAIL/PRODUCT_BUG 是假 Bug（把引擎的
        #   生成缺口写成产品缺陷）。故一律判 SKIP(ENGINE_COVERAGE_GAP)——既不记通过
        #   也不记缺陷，只记"该用例未真正测到"。参照 pytest 的 collection/setup 语义
        #   与 Playwright 的 actionability：前置未达成时不得断言被测系统有问题。
        #   高精度保证：只要用例施加过任一状态变更动作，本判据一律放行，交由既有判定链。
        _unreachable = ""
        with guard(
            "_execute_one_intent: from src.pipeline.utils.case_self_consi", log=_log
        ):
            from src.pipeline.utils.case_self_consistency import (
                ASSERTION_UNREACHABLE,
                assess,
                unreachable_reason,
            )

            _scenario = (intent_payload or {}).get("scenario_type") or (
                case_meta or {}
            ).get("scenario_type") or ""
            _checks = (intent_payload or {}).get("checks") or []
            _acts = (intent_payload or {}).get("actions") or steps
            _scope = (intent_payload or {}).get("interaction_scope") or (
                case_meta or {}
            ).get("interaction_scope") or ""
            if (
                assess(_acts, _checks, _scenario, _scope) == ASSERTION_UNREACHABLE
            ):
                _unreachable = unreachable_reason(_scenario, _checks, _acts)

        # ★ 通用修复(2026-07-20): status 对齐 verdict 口径
        #   - case_passed=True AND hard_verified=True → PASS
        #   - case_passed=True AND hard_verified=False → MANUAL_REVIEW（无硬证据，不能标 PASS）
        #   - case_passed=False → FAIL（由后续 classify_failure 进一步细分）
        # ★ 声明期望未适用（N/A 跳过）不得借道结构性断言记 PASS：
        #   见 `has_not_applicable_assertion` 的 why（⊘ 抽取同一事实源）。
        _assertion_na = has_not_applicable_assertion(step_results)
        # ★ 根因修复(2026-10-28) 无断言不得记 PASS：
        #   现象: checks 为空的 Agent 用例（实测 TC-REQ-010/011/020/065-I01）以引导层
        #     "页面已加载 / 任意错误条出现"为证据判 PASS（conf 0.88~0.93）；该证据与需求
        #     主张无因果绑定（如"返回统一错误结构：code、message"却由表单
        #     ".field-error: 价格必须是数字"背书）→ 结构性假绿。
        #   根因(5层): L1 现象=假 PASS；L2 直接=`_explore_task.test_method.verifications`
        #     的通用探针（page_loaded/错误条检测）被当作需求义务的证据；L3 为何: 用例
        #     checks 为空 → 判定链没有任何"需求声明的可观察事实"可消费，只能退化为探针；
        #     L4 为何反复: 生成端 `_checks_from_expected` 在无 needle 时发 semantic_check，
        #     但 AI 直出 intent / 外部导入/回填等路径可产出 checks=[] 且无人兜底；
        #     L5 根本: PASS 的充分条件里缺了"本用例至少声明一条可执行断言"。
        #   修复: checks 为空 ⇒ neither PASS nor FAIL —— 不记通过（探针不承载需求义务），
        #     也不记缺陷（未对产品行为做出任何判定），统一 SKIP(ENGINE_COVERAGE_GAP)，
        #     与既有"前置未达成/断言 N/A"同口径。
        #   通用性: 只问"是否声明了可执行断言"，跨系统/行业/页面成立，零业务词表。
        #   判据单点收口：`_assertion_verdict.declares_executable_assertion`（Agent 路径同源）。
        _no_declared_assertion = not declares_executable_assertion(intent_payload)
        # 前置数据未满足（造数失败）：断言对象根本不存在，判定两向都不成立
        _data_gap = str(
            getattr(engine._page, "_case_data_precondition_gap", "") or ""
            if engine._page is not None
            else ""
        )
        if _unreachable or _data_gap or _no_declared_assertion:
            status = "SKIP"
        elif case_passed and _hard_verified_any and _assertion_na:
            status = "SKIP"
        elif case_passed and _hard_verified_any:
            status = "PASS"
        elif not case_passed:
            status = "FAIL"
        else:
            # case_passed=True 但无 hard_verified → MANUAL_REVIEW
            # 此时 all_passed=True 且 step_results 全 passed，但缺少硬证据
            status = "MANUAL_REVIEW"
        failure_category = (
            "ENGINE_COVERAGE_GAP"
            if (_unreachable or _assertion_na or _data_gap or _no_declared_assertion)
            else ""
        )
        failure_chain_json = ""
        if _no_declared_assertion:
            failure_reason = (
                "【SKIP】用例未声明任何可执行断言（checks 为空）→ 引导层通用探针"
                "（页面已加载/任意错误条）不能作为需求义务的证据：不记 PASS（防假绿），"
                "也不记 FAIL（防假 Bug）；需补齐断言或转人工补测"
            )
        elif _data_gap:
            failure_reason = (
                "【SKIP】用例前置数据未满足（%s）→ 断言对象不存在，"
                "不记 PASS（防假绿），也不记 FAIL（防假 Bug）" % _data_gap
            )
        elif _assertion_na:
            failure_reason = (
                "【SKIP】用例声明的断言在本次执行的页面状态下不适用（N/A 跳过），"
                "未对期望做出任何判定 → 不记 PASS（防假绿），也不记 FAIL（防假 Bug）"
            )
        else:
            failure_reason = ""
        chain = None  # ★ F1: 失败链路对象（仅 not case_passed 时构建，供 AI 根因增补复用）
        with guard("_execute_one_intent: from src.core.config_loader import g", log=_log, level=logging.WARNING):
            from src.core.config_loader import get_current_user
            from src.users.audit import record_execution
            from src.backend.state import _thread_run_id

            uid = get_current_user() or "anonymous"
            run_id = getattr(_thread_run_id, "value", "") or ""
            if not case_passed and not _data_gap:
                failure_reason = "; ".join(
                    r["error"] for r in step_results if r["error"]
                )

            # ★ 失败分类：区分被测系统bug vs 执行系统bug
            #   （自洽契约/前置数据缺口已判 SKIP 时跳过：无从归因产品/引擎执行）
            if not case_passed and not _unreachable and not _data_gap:
                with guard("_execute_one_intent: from src.pipeline.steps.step4.failur", log=_log):
                    from src.pipeline.steps.step4.failure_classifier import (
                        FailureChain,
                        classify_failure,
                        build_failure_reason,
                        is_network_error_text,
                    )

                    chain = FailureChain()
                    # 从 step_results 重建失败链路
                    has_playwright_error = False
                    has_timeout = False
                    has_network_error = False
                    for r in step_results:
                        if not r.get("passed"):
                            err = (r.get("error") or "").lower()
                            if "timeout" in err or "timed out" in err:
                                has_timeout = True
                            # ★ BUG-1(2026-08-05): 统一走 is_network_error_text 判定，
                            #   覆盖 net::ERR_CONNECTION_TIMED_OUT 等更多网络错误标记
                            if is_network_error_text(err):
                                has_network_error = True
                            if (
                                "playwright" in err
                                or "browser" in err
                                or "target closed" in err
                            ):
                                has_playwright_error = True
                            chain.add_step(
                                strategy=r.get("action")
                                or r.get("description")
                                or "unknown",
                                success=False,
                                detail=r.get("error", ""),
                            )
                    # ★ BUG-1(2026-08-05): 导航阶段网络错误信号（lifecycle.goto 捕获
                    #   net::ERR_* 时置位 engine._last_network_error）→ 并入归因信号。
                    #   修复前: goto 失败仅 log+return False，错误文本未必进 step_results，
                    #   归因收不到 network_error → 误判 HARNESS_ERROR。
                    if getattr(engine, "_last_network_error", False):
                        has_network_error = True
                    failure_category, status = classify_failure(
                        chain,
                        playwright_error=has_playwright_error,
                        timeout_error=has_timeout,
                        network_error=has_network_error,
                    )
                    # ★ 根因修复（2026-09-23）D8-结构化路径补齐 —— 产品缺陷被记 ERROR：
                    #   D8/D8b 只改在 Agent 路径（`_agent_checks._finalize_agent_failure`），
                    #   而本路径（结构化断言执行）**直接** classify_failure → 断言已真实
                    #   执行并给出✗度量的用例仍被判 HARNESS_ERROR。实测 TC-REQ-001-CP01/
                    #   UX01/SEC01（viewport_rendering_check 移动端重叠 44px /
                    #   click_target_size_check 不达标 / text_contains 0 命中）
                    #   在 round3、round4 均为 ERROR，与同一行 ai_root_cause=PRODUCT_BUG 矛盾。
                    #   修复：判据抽为共享单点 `_assertion_verdict`（两路径同源），
                    #   存在"已执行且给出否定结论的断言" → PRODUCT_BUG/FAIL；
                    #   纯动作/定位缺口不升格（防凭空制造假 Bug，见该模块文档）。
                    if has_executed_assertion_verdict(step_results):
                        failure_category, status = "PRODUCT_BUG", "FAIL"
                    # ★ 保留既有语义（D18 前提保护）——「权威需求未实现」必须仍是
                    #   PRODUCT_BUG。原文案由引擎自身产出（`_agent_executor.py:395`
                    #   "需求已定义但系统未实现/不可达: … 需求未得到满足"），语义为
                    #   "需求已定义(is_implicit=0)但系统无该功能"= 产品覆盖缺口。
                    #   D18 收窄了无差别升格后，此类若走本（结构化）路径会回落
                    #   classify_failure，而单步失败链路会被判 HARNESS_ERROR(ERROR)
                    #   → 把最典型的产品缺口降级成引擎错误。故按引擎自产标记显式保留：
                    #   只认这一条语义明确的引擎文案，不做通用兜底（避免again 无差别升格）。
                    elif "需求已定义但系统未实现" in str(failure_reason or ""):
                        failure_category, status = "PRODUCT_BUG", "FAIL"
                    # ★ 根因修复（2026-09-23）D18 —— 引擎不可评估的失败不得记产品缺陷：
                    #   上一条判据原为「error 含"结构化校验失败" 即升格 PRODUCT_BUG」，
                    #   是**无差别升格**（结构化校验失败既可能是产品不满足，也可能是
                    #   引擎根本没评估成功）。round5 实测 60 条 FAIL **全部**被判
                    #   PRODUCT_BUG，其中 19 条失败项全部是引擎侧不可评估：
                    #     · TC-REQ-00X-SEC01 ×9：唯一失败轨=API「声明了API校验点但无API调用
                    #       发生」，同行 network_logs 只有 GET / 与 GET /products（零 XHR）
                    #       → 纯 SSR 靶子被声明 API 断言 ⇒ 断言不可评估；
                    #     · TC-REQ-006/007-V01 ×2：`element_exists:✗(查询异常:
                    #       Locator.count: Unexpected token ":")` → 选择器含伪元素
                    #       `::before`，任何页面都必抛 ⇒ 引擎选择器非法（见 D21）；
                    #     · TC-REQ-006-B01/N01/PF01/PF02 ×8：`未知检查类型`（引擎无
                    #       handler）/ `AI生成SQL失败`（引擎侧解析失败）。
                    #   修复：改用共享单点谓词——无任何"产品不满足"证据、失败项全为
                    #   引擎不可评估 ⇒ SKIP(ENGINE_COVERAGE_GAP)。既不记 FAIL（防假 Bug，
                    #   直接回应用户"别把引擎深度问题当产品缺陷"），也不记 PASS（防假绿）。
                    elif has_only_engine_gap_verdict(step_results):
                        failure_category, status = "ENGINE_COVERAGE_GAP", "SKIP"
                        failure_reason = (
                            "【SKIP】失败项全部为引擎不可评估的检查"
                            "（未知检查类型/执行器异常/断言不适用），无产品缺陷证据"
                            " → 不记 FAIL（防假 Bug）"
                        ) + (
                            "；原判定: " + str(failure_reason) if failure_reason else ""
                        )
                        _log.info(
                            "[Execution] 引擎缺口转 SKIP: case=%s 原分类=%s",
                            case_id,
                            failure_category,
                        )
                    # 断言均标注"非判定态"（⊘ N/A 跳过/未验证）且无真实负向判定
                    # ⇒ 无产品缺陷证据，判 SKIP（防假 Bug，也防假绿）。
                    elif has_no_negative_verdict(step_results):
                        failure_category, status = "ENGINE_COVERAGE_GAP", "SKIP"
                        failure_reason = (
                            "【SKIP】断言均标注为非判定态（N/A 跳过/未验证/无有效断言），"
                            "无任何真实负向判定 → 不记 FAIL（防假 Bug），也不记 PASS（防假绿）"
                        ) + (
                            "；原判定: " + str(failure_reason) if failure_reason else ""
                        )
                        _log.info(
                            "[Execution] 非判定态转 SKIP: case=%s", case_id
                        )
                    failure_chain_json = chain.to_json()
                    if not failure_reason:
                        failure_reason = build_failure_reason(chain)

            if _unreachable:
                # 自洽契约判 SKIP 的归因（覆盖上面由 step error 拼出的失败原因，
                # 因为失败原因描述的是"断言落空"，而真实原因是"用例没做该做的操作"）
                failure_reason = (
                    "【SKIP】用例自洽契约：断言前置未满足 → " + _unreachable
                )
                _log.info(
                    "[Execution] 断言前置未满足转 SKIP: case=%s reason=%s",
                    case_id,
                    _unreachable[:120],
                )

            # ★ v25 根因Z5(2026-07-21): 从 _verify_result 提取三轨 evidence（无论 PASS/FAIL）
            #   原 bug: 只在 check 失败时提取 expected/actual/details，PASS 时全空 → 假PASS无证据
            #   修复: 从 step_results[-1]["_verify_result"] 提取 ui/api/db 三轨 evidence，
            #         无论 PASS/FAIL 都写入 executions 表，确保可分析性。
            #   ★ v25 根因Z5-补丁(2026-07-21): actual在PASS时也提取ui轨evidence
            #     原 bug: actual只从失败轨提取（not passed），PASS时actual永远空
            #     修复: actual记录所有轨的evidence（无论PASS/FAIL），这样PASS时也能看到实际验证了什么
            _check_type = "browser_exec"
            # ★ P0-fix(2026-07-24): evidence 提取拆到独立文件（§4.3 单文件≤800行）
            #   原 bug: _verify_result=None 时 evidence 全空 → 空 evidence FAIL（§13.4 违规）
            #   修复: 调用 extract_evidence_from_step_results，含 None 兜底逻辑
            _expected = ""
            _actual = ""
            _check_details = []
            with guard("_execute_one_intent: from src.pipeline.steps.step4.browse", log=_log, level=logging.WARNING):
                from src.pipeline.steps.step4.browser_core.single_mixins._evidence_extractor import (
                    declared_expectation,
                    extract_evidence_from_step_results,
                )

                _expected, _actual, _check_details = extract_evidence_from_step_results(
                    step_results,
                    declared_expected=declared_expectation(intent, case_meta),
                )
            _details_json = ""
            try:
                import json as _json2

                _details_json = (
                    _json2.dumps(_check_details, ensure_ascii=False)
                    if _check_details
                    else ""
                )
            except Exception:
                _details_json = ""

            # ★ F1 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：
            #   失败分类/根因分析接入 AI —— enrich_root_cause_ai 规则判定后增补，
            #   合并进 details JSON（追加 ai_* 键），**绝不覆盖** status/
            #   failure_category/failure_reason。任何失败 fail-open 返回 {} 等同现状。
            if not case_passed and chain is not None and status in ("FAIL", "ERROR"):
                with guard("_execute_one_intent: from src.pipeline.steps.step4.failur", log=_log):
                    from src.pipeline.steps.step4.failure_classifier import (
                        enrich_root_cause_ai as _enrich_ai,
                    )

                    _ai_enrich = _enrich_ai(
                        chain,
                        expected=_expected,
                        actual=_actual,
                        failure_reason=failure_reason or "",
                        category=failure_category or "",
                        status=status or "",
                    )
                    if _ai_enrich:
                        _det = _json2.loads(_details_json) if _details_json else {}
                        if isinstance(_det, list):
                            _det = {"_items": _det}
                        if not isinstance(_det, dict):
                            _det = {}
                        _det.update(_ai_enrich)
                        _details_json = _json2.dumps(_det, ensure_ascii=False)
                        _log.info(
                            "[Execution] AI 根因增补合并 details: case_id=%s keys=%s",
                            case_id,
                            ",".join(
                                sorted(k for k in _ai_enrich if k.startswith("ai_"))
                            ),
                        )

            # ★ C5 [根因修复-证据接线/D-2026-09-04]: 将捕获的 console/network 证据真正
            #   落入该用例 details JSON（追加 evidence.console_logs / evidence.network_logs
            #   键，仅非空时写入，绝不移除/覆盖 status/expected/actual 等既有字段）。
            #   并行路径取 worker 缓冲（contextvars task-local，get_worker_errors 同时含
            #   console.error 与 pageerror）；串行路径回落 engine 证据缓冲（复用
            #   lifecycle.get_execution_evidence()，缺方法则直读 engine._console_logs/_
            #   network_logs）。任何异常仅记日志，不向执行路径冒泡（fail-open）。
            _console_evidence = []
            _network_evidence = []
            try:
                _wcv = get_worker_errors()
                if _wcv is not None:
                    # 并行：worker 自己的 console/pageerror 缓冲
                    _console_evidence = list(_wcv or [])
                    _wnv = get_worker_network()
                    _network_evidence = list(_wnv or []) if _wnv is not None else []
                else:
                    # 串行：回落 engine 证据缓冲
                    _ser_ev = None
                    _gee = getattr(engine, "get_execution_evidence", None)
                    if callable(_gee):
                        _ser_ev = _gee()
                    _ser_ev = _ser_ev if isinstance(_ser_ev, dict) else {}
                    _console_evidence = (
                        list(_ser_ev.get("console") or [])
                        or list(_ser_ev.get("console_errors") or [])
                        or list(getattr(engine, "_console_logs", []) or [])
                        or list(getattr(engine, "_console_errors", []) or [])
                    )
                    _network_evidence = list(
                        _ser_ev.get("network") or []
                    ) or list(getattr(engine, "_network_logs", []) or [])
            except Exception as _cev_e:
                _log.debug("[C5] 证据缓冲读取异常(忽略): %s", _cev_e)
                _console_evidence = []
                _network_evidence = []
            # ★ 根因修复(2026-10-28) 证据落库体积契约（唯一入口 `_evidence_budget`）：
            #   根因(3 层追问)：① 表层——118 条用例的 executions 行整行丢失；
            #   ② 中层——(1406, "Data too long for column 'details'")，details 为
            #     TEXT(65535 字节)，而此处把缓冲里**全部** console/network 条目原样拼入；
            #   ③ 根因——证据写入没有体积契约，且把「页面级存量事实」（同一页面任何
            #     用例都会看到的装载期报错）逐用例复制一份，体积随用例数线性放大。
            #   修复：只落「用例可归因」条目（判据 `js_load_phase.is_case_attributable`，
            #   GAP-01 同源），页面级事实压成计数+签名摘要；再施加上限（容量契约），
            #   超限只记丢弃条数 → 任意被测系统的日志风暴都不会击穿存储。
            with guard("_execute_one_intent: _c5_evidence = {}", log=_log):
                from src.pipeline.steps.step4.browser_core.single_mixins._evidence_budget import (
                    pack_case_evidence,
                )

                _c5_evidence = pack_case_evidence(
                    _console_evidence, _network_evidence
                )
                if _c5_evidence:
                    _det = _json2.loads(_details_json) if _details_json else {}
                    if isinstance(_det, list):
                        _det = {"_items": _det}
                    if not isinstance(_det, dict):
                        _det = {}
                    _det["evidence"] = _c5_evidence
                    _details_json = _json2.dumps(_det, ensure_ascii=False)
                    _log.info(
                        "[C5] 用例 %s 证据落库: 原始 console=%d network=%d → 落库 console=%d"
                        " 页面级摘要=%d network=%d",
                        case_id,
                        len(_console_evidence),
                        len(_network_evidence),
                        len(_c5_evidence.get("console_logs") or []),
                        int(
                            (_c5_evidence.get("page_level_console") or {}).get(
                                "total", 0
                            )
                        ),
                        len(_c5_evidence.get("network_logs") or []),
                    )

        execution_id = record_execution(
            user_id=uid,
            project=project,
            case_id=case_id,
            status=status,
            case_asset_file=case_meta.get("case_asset_file", ""),
            case_row=_safe_int(case_meta.get("case_row", 0)),
            screenshot="",
            failure_reason=failure_reason,
            run_id=run_id,
            failure_category=failure_category,
            failure_chain=failure_chain_json,
            check_type=_check_type,
            expected=_expected,
            actual=_actual,
            details=_details_json,
        )
        # ★ 失败根因分类（diagnosis 列）：将异常文本归类，便于按被测系统问题类别聚合
        if not case_passed and (failure_reason or failure_chain_json):
            with guard("_execute_one_intent: from src.pipeline.steps.step4.diagno", log=_log):
                from src.pipeline.steps.step4.diagnosis import (
                    classify_diagnosis,
                    ensure_executions_diagnosis_column,
                )

                ensure_executions_diagnosis_column()
                _diag = classify_diagnosis(
                    (failure_reason or "") + " " + (failure_chain_json or "")
                )
                if _diag and _diag != "UNKNOWN":
                    from src.core.db_manager import get_connection

                    _conn = get_connection()
                    try:
                        _conn.execute(
                            "UPDATE executions SET diagnosis=%s WHERE id=%s",
                            (_diag, execution_id),
                        )
                        _conn.commit()
                    finally:
                        with guard("_execute_one_intent: _conn.close()", log=_log):
                            _conn.close()
        # ★ 置信度闸门(拆到 _confidence_gate.py, §4.3 单文件≤800行)
        from src.pipeline.steps.step4.browser_core.single_mixins._confidence_gate import (
            apply_confidence_gate,
        )

        case_passed = apply_confidence_gate(
            status,
            case_passed,
            execution_id,
            case_id,
            case_meta,
            intent_payload,
            intent,
            intent_desc,
            steps,
            step_results,
            expected=_expected,
            actual=_actual,
            details=_details_json,
            security_findings=security_findings,
            # ★ P0-3 增量 [未复核-需 GLM/DeepSeek 把关]：深断言信号标记
            #   由 step4_executor 在 apply_adi_gate 后写入用例 dict（默认无此键 → False）。
            adi_signal=bool(intent.get("adi_signal", False)),
        )
        _log.info(
            "[Execution] 记录到 executions: %s status=%s category=%s execution_id=%s",
            case_id,
            status,
            failure_category or "N/A",
            execution_id,
        )

        # ★ P1-5(2026-07-25): oracle_results 持久化(执行期真值闭环, §13.4 职责边界内)
        #   逻辑拆到 _oracle_persist.py（§4.3 单文件≤800行）
        from src.pipeline.steps.step4.browser_core.single_mixins._oracle_persist import (
            persist_oracle_after_execution,
        )

        persist_oracle_after_execution(
            project, execution_id, case_passed, status, step_results, case_id=case_id
        )

        # ★ 指标: 记录成功用例的步骤数（用例复杂度）
        if case_passed and steps:
            try:
                from src.extensions.metrics_collector import metrics_collector

                metrics_collector.record_case_complexity(
                    project=project,
                    run_id=run_id,
                    case_id=case_id,
                    step_count=len(steps),
                )
            except Exception as _e:
                _log.debug("record_case_complexity 失败: %s", _e, exc_info=True)

        # --- ★ Batch4: 幻觉样本沉淀（失败用例分类后写入 ai_generation_logs）---
        # 触发 test_rules 自动生成链路: ai_generation_logs 积累 >=3 条 → analyze_and_update_rules()
        if not case_passed:
            with guard("_execute_one_intent: from src.extensions.quality.hallucin", log=_log):
                from src.extensions.quality.hallucination_logger import (
                    hallucination_logger as _hl,
                )

                _h_class = _hl.classify_failure(intent, step_results, failure_reason)
                # 仅真实幻觉入样本库：env（环境/AI 超时等）与 unknown（待人工标注）不自动沉淀，
                # 避免非幻觉失败污染闭环反馈与规则自动更新
                if _h_class == "hallucination":
                    _h_intent = {
                        "case_id": case_id,
                        "req_id": intent.get("req_id", "")
                        or case_meta.get("req_id", ""),
                        "module": intent.get("module", "")
                        or case_meta.get("module", ""),
                        "title": intent_title,
                        "steps": steps,
                    }
                    _h_failure_info = {
                        "reason": failure_reason,
                        "classification": _h_class,
                    }
                    _hl.save_hallucination_sample(_h_intent, _h_failure_info)

        # --- ★ v7: 证据引擎校准回写 ---
        with guard("_execute_one_intent: from src.core.pipeline_config import", log=_log):
            from src.core.pipeline_config import pipeline_config as _ppl_cfg

            if getattr(_ppl_cfg, "evidence_engine_enabled", True):
                from src.pipeline.evidence.calibration import (
                    record_calibration as _record_cal,
                )

                _cal_entries = []
                _im = case_meta or {}
                # 共识级别
                _consensus = str(
                    _im.get("_consensus_level", "")
                    or intent.get("_consensus_level", "")
                    or ""
                ).strip()
                if _consensus:
                    _cal_entries.append(
                        {
                            "key": "consensus_level",
                            "value": _consensus,
                            "correct": status == "PASS",
                        }
                    )
                # 视角数量
                _pc = _im.get("_perspective_count", intent.get("_perspective_count", 0))
                if _pc and int(_pc) > 0:
                    _pc_val = "1" if int(_pc) == 1 else ("2" if int(_pc) == 2 else "3+")
                    _cal_entries.append(
                        {
                            "key": "perspective_count",
                            "value": _pc_val,
                            "correct": status == "PASS",
                        }
                    )
                # source_quote
                _sq = str(
                    _im.get("source_quote", "") or intent.get("source_quote", "") or ""
                ).strip()
                _sq_val = "yes" if _sq else "no"
                _cal_entries.append(
                    {
                        "key": "source_quote",
                        "value": _sq_val,
                        "correct": status == "PASS",
                    }
                )
                # 风险等级
                _risk = str(
                    intent.get("risk_level", "")
                    or case_meta.get("risk_level", "")
                    or ""
                ).strip()
                if _risk:
                    _cal_entries.append(
                        {
                            "key": "risk_level",
                            "value": _risk,
                            "correct": status == "PASS",
                        }
                    )
                # 意图类型
                _itype = str(
                    intent.get("case_type", "")
                    or intent.get("type", "")
                    or case_meta.get("case_type", "")
                    or ""
                ).strip()
                if _itype:
                    _cal_entries.append(
                        {
                            "key": "case_type",
                            "value": _itype,
                            "correct": status == "PASS",
                        }
                    )
                # 需求类型（从 trace 链路获取）
                _rtype = str(
                    intent.get("requirement_type", "")
                    or case_meta.get("requirement_type", "")
                    or ""
                ).strip()
                if _rtype:
                    _cal_entries.append(
                        {
                            "key": "requirement_type",
                            "value": _rtype,
                            "correct": status == "PASS",
                        }
                    )
                if _cal_entries:
                    _record_cal(project, _cal_entries)

        # --- 记录支配度指标 ---
        with guard("_execute_one_intent: self._record_intent_dominance_metric", log=_log, level=logging.WARNING):
            self._record_intent_dominance_metrics(case_id, case_meta, step_results)

        # --- 插件：after_intent ---
        await self._plugin_manager.execute_hook(
            "after_intent",
            case_id=case_id,
            case_meta=case_meta,
            passed=case_passed,
            step_results=step_results,
            engine=engine,
            ai=ai,
            intent_elapsed=intent_elapsed,
        )

        _log.info(
            "[Step %d/%d] 结果: %s (%.1fs)",
            step_num,
            total,
            "通过" if case_passed else "失败",
            intent_elapsed,
        )

        # ★ 每个用例终态截图落库（PASS/FAIL/MANUAL_REVIEW 均留存，复用 execution_screenshots）
        with guard("_execute_one_intent: await self._save_case_screenshot( ca", log=_log, level=logging.WARNING):
            await self._save_case_screenshot(
                case_id, status, project, run_id, step_num_display
            )

        return {
            "case_id": case_id,
            "passed": case_passed,
            # ★ 终态落结构化字段：下游（serial/parallel 计数与 §10.1 即停）只读不推。
            #   缺此字段时下游只能用 passed 布尔重推，SKIP/BLOCKED 会被误判为失败。
            "status": status,
            # ★ C5 [未复核-需 GLM/DeepSeek 把关]: 用例级网络证据（并行=worker 缓冲，
            #   串行=[]），供执行证据留存/关联 execution。
            "_network_evidence": _case_network_logs,
            "elapsed": round(intent_elapsed, 2),
            "step_results": step_results,
            "error": (
                ""
                if case_passed
                else "; ".join(r["error"] for r in step_results if r["error"])
            ),
            "failure_category": failure_category,
            "failure_reason": failure_reason,
            "security_findings": security_findings,
            "_generation_source": intent.get("_generation_source", ""),
        }
