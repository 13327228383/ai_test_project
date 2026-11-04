# -*- coding: utf-8 -*-
"""单意图执行 — AI 多轮循环 mixin。

职责：
- _run_ai_round_loop: AI 多轮循环执行兜底路径
  * SmartLocator 匹配、DOM 获取、截图、Prompt 构建、AI 调用、响应解析、动作执行、页面稳定等待

被 _base._execute_one_intent 在步骤循环的 else 分支调用。
"""

import asyncio
from src.core.guarded import guard
import json
import logging
import time as _time

from src.core.config_loader import config
from src.core.pipeline_config import pipeline_config
from src.pipeline.steps.step4.browser_core._smart_locator import match_step_to_element
from src.pipeline.steps.step4.action_hooks import run_action_with_hooks
from src.pipeline.steps.step4.browser_tools.locator import _locate_by_hint

# ★ T-003: 复用 selectors.py 的 CSS 转义函数
from src.pipeline.steps.step4.playwright_engine.selectors import _escape_css_str

_log = logging.getLogger(__name__)


# ★ 架构债清理（2026-07-25）: 模块级辅助函数已拆分到 _ai_loop_utils.py
#   类内辅助方法已拆分到 _ai_loop_helpers_mixin.py（_AiLoopHelpersMixin）
#   阶段函数已拆分到 _ai_loop_phases.py（§4.3 单文件≤800行）
from ._ai_loop_utils import (  # noqa: F401
    _enrich_action_tags,
    _find_element_tag_by_selector,
    _selector_to_target_hint,
    _decide_to_cache_action,
    _extract_available_elements,
)
from ._ai_loop_helpers_mixin import _AiLoopHelpersMixin
from ._ai_loop_phases import (
    _record_ai_loop_entry,
    _get_dom_with_retry,
    _build_smart_locator_dom,
    _try_upgrade_strategies,
    _build_ai_prompt,
    _query_op_cache_v2,
    _parse_ai_response,
    _check_repeat_action,
    _record_browser_ai_round,
    _record_op_cache_v2,
    _record_ai_loop_pass,
    _execute_0token_smart_locator,
)


async def _page_fingerprint(page) -> str:
    """页面指纹（URL + 可见文本长度 + 文本哈希 + 表单控件值哈希）。

    用于"重复动作是否真的没让页面变化"的**实测**判据（原实现只比动作签名，
    却声称"页面没有变化"）。取最便宜的可观测信号：URL 与 body 可见文本。
    表单控件值必须纳入：fill/select 等动作只改控件值、不改正文文本，
    若指纹只看正文会把"已填入查询词"误判为"页面无变化"（实测 TC-REQ-002-UX02）。
    任何异常一律返回 ""（非阻断，退化为原有签名判据）。
    """
    if page is None:
        return ""
    try:
        _url = page.url or ""
        _txt = await page.locator("body").inner_text(timeout=2000)
        _txt = _txt or ""
        _form_vals = await page.eval_on_selector_all(
            "input, textarea, select",
            "els => els.map(e => (e.tagName||'').toLowerCase() + ':' + (e.value !== undefined ? e.value : ''))",
        )
        _form = "|".join(sorted(_form_vals)) if _form_vals else ""
        return "%s|%d|%s|%s" % (
            _url, len(_txt), str(hash(_txt))[:8], str(hash(_form))[:8])
    except Exception:  # noqa: BLE001
        return ""


async def _verify_form_value(engine, selector: str, expected: str) -> bool:
    """fill 动作的**可验证判据**：目标控件当前值 == 期望值。

    根因: 页面指纹无法区分"把已有值再填一遍"这类幂等操作（指纹本就不变），
      于是重复检测以"页面没有变化"为由判其失败——但填充的正确判据是
      "目标字段的值是否等于期望值"，与页面是否重绘无关。通用性：纯表单语义。
    """
    if not selector or engine is None or getattr(engine, "_page", None) is None:
        return False
    try:
        value = await engine._page.locator(selector).first.input_value(timeout=2000)
        return str(value or "").strip() == str(expected or "").strip()
    except Exception:  # noqa: BLE001
        return False


class _BrowserCoreSingleAiLoopMixin(_AiLoopHelpersMixin):
    """AI 多轮循环执行能力。

    ★ 架构债清理（2026-07-25）: 多继承 _AiLoopHelpersMixin 获取辅助方法
      (_enrich_page_explorer / _scroll_and_find / _try_alternative_entries /
       _try_library_fast_path / _try_menu_fallback_for_fail)。
    本类仅保留 _run_ai_round_loop 主体。
    """

    async def _run_ai_round_loop(
        self,
        *,
        step,
        step_desc,
        step_expected,
        case_meta,
        meta_text,
        engine,
        ai,
        step_passed,
        step_error,
        all_passed,
        structured_all_failed=False,
    ):
        """执行 AI 多轮循环（兜底路径）。

        参数通过关键字传入，保持与原始局部变量的语义一致。
        返回 (step_passed, step_error, all_passed) 三元组。

        structured_all_failed: 当所有结构化动作均失败时，限制 AI 最多 3 轮并启用快速失败。
        """
        ai_round = 0
        ai_passed = False
        ai_error = ""
        last_dom = ""
        last_dom_elements = []  # ★ 持久化DOM元素列表，用于失败时的错误报告
        last_screenshot = ""
        import_hint = ""
        table_hint = ""

        # ★ 结构化全部失败时，限制 AI 最多 5 轮（快速失败，避免空转 8 轮）
        #   通用修复 (2026-07-17): 从 3 轮提升到 5 轮，SPA 导航场景下需要更多轮次恢复
        _effective_max_rounds = 5 if structured_all_failed else self.MAX_AI_ROUNDS
        _consecutive_element_fail = 0  # 连续同元素失败计数

        # ★ Batch10: op_cache 状态跟踪
        _cache_hit = False  # 本轮是否命中缓存
        _last_ai_action = None  # 最后一轮 AI 决策（供循环结束后缓存）
        _last_dom_for_cache = None  # 最后一轮 AI 决策时的 DOM（缓存 key 计算用）
        # ★ : 经验复用修复——标准 step loop 路径 cache 计数
        #   根因: _query_op_cache_v2 返回 _cache_hit 布尔值但从未累加到 intent 字典，
        #   导致 _confidence_gate.py 读取 intent.get("op_cache_hits", 0) 永远为 0。
        #   修复: 累计 cache 命中/未命中次数，在方法结束时写入 self 供 _base.py 消费。
        _cache_hit_count = 0
        _cache_miss_count = 0
        # ★ P1-8 修复(2026-07-25): repair 拍 — 命中缓存但执行失败时删除缓存
        #   根因: 三拍设计中 repair 拍缺失，命中后失败不删缓存 → 下次继续命中坏缓存
        #   通用性: repair_info 是 (project, version_id, intent_hash) 三元组，通用元组
        #   覆盖性: 单元B 命中时赋值，失败分支消费；成功分支不消费（保留缓存）
        _cache_repair_info: (
            tuple
        ) = ()  # 命中时记录 (project, vid, intent_hash) 供失败 repair

        # ★ 指标: 记录 AI loop 兜底进入（查表未命中后走 AI 决策）
        _record_ai_loop_entry(case_meta)

        # ★ 连续无变化检测：避免 AI 在相同无效动作上空转 8 轮
        _max_no_change_rounds = int(
            getattr(pipeline_config, "ai_loop_max_no_change_rounds", 2)
        )
        _last_action_signature = ""
        _no_change_count = 0
        # ★ F2 根因修复(2026-09-26)：重复检测原先**只看动作签名**（action_type+
        #   selector+value），从不观测页面是否真的变化，却把提示写成"页面没有变化"
        #   —— 实测 TC-REQ-001-PF02/UX02 因该误导性判据进入重置-终止链被判 ERROR。
        #   修复：每轮决策前取一次页面指纹（URL + 可见文本长度），与上一轮对比；
        #   页面确有变化时重置重复计数（说明上一动作生效，AI 未空转）。
        _last_fingerprint = ""
        # ★ fill 幂等判据：上一轮 fill 是否**可验证地**把目标字段写成了期望值。
        #   与页面指纹互补（指纹看不出幂等填充），供重复检测判定动作是否已生效。
        _last_form_effective = False
        # ★ Fix G(2026-09-27): 页面指纹「连续无进展」计数与容忍轮数。
        #   根因: 既有判据只拦"同动作重复"（_no_change_count）与"无 DOM 动作"
        #   （_IDLE_TIMEOUT_SEC），拦不住"每轮动作不同、页面指纹（URL+可见文本）
        #   却完全不变"的长尾空转（实测单条 AI loop 跑满 8 轮 6.4min）。
        #   通用性: 只看页面是否真的变化，与业务/系统无关。
        _no_progress_count = 0
        _max_no_progress_rounds = int(
            getattr(pipeline_config, "ai_loop_max_no_progress_rounds", 3)
        )
        # ★ Fix B(2026-09-27): 早停判据补"成功交互"维度——指纹无变化 ≠ 无法推进：
        #   表单填充/点击/提交通常成功派发后页面正文可能确实不变（查询空结果、同页
        #   持续操作），此时早停会误杀合法流程（实测 002-UX02/008-UX02 假 ERROR）。
        #   仅当连续 N 轮**指纹不变且无成功 DOM 交互**才算真空转（长尾兜圈）；
        #   本轮是否成功交互由 _last_dom_action_time 在回合末回填。
        _last_round_interacted = True  # 首轮不早停（无上一轮可比）
        # ★ 通用修复(2026-07-20): 重置次数计数器 — 限制 AI 只能有1次重置机会
        #   原bug: 每次达到阈值都重置 _no_change_count，给 AI "又一次机会"，
        #          导致 AI 永远无法跳出循环（click("辅材配方")→重置→又click("辅材配方")→又重置...）
        #   修复: 第一次达到阈值时重置（给AI一次基于真实DOM重新决策的机会），
        #         第二次达到阈值时直接 break，避免无限循环浪费 token
        #   通用性: 适用于所有"AI 陷入相同操作循环"场景，不依赖业务逻辑
        _no_change_reset_count = 0
        _MAX_RESET_COUNT = 1  # 最多重置1次

        # ★ v3.0.1 (2026-07-10): 1分钟无DOM动作判致命bug
        #   规则/指令执行时，若墙钟超过 _IDLE_TIMEOUT_SEC 无任何成功 DOM 操作，判定为致命bug并中断
        _IDLE_TIMEOUT_SEC = int(getattr(pipeline_config, "step4_idle_timeout_sec", 60))
        _last_dom_action_time = _time.time()

        # ★ Fix G(2026-09-27): AI loop 总墙钟预算熔断 —— 单条用例 AI 兜底最多消耗的秒数。
        #   根因: 实测单条 TC-REQ-002-UX02 AI loop 跑满 8 轮共 6.4min（占全轮 69%），
        #   期间既有"连续无DOM动作"检测（_IDLE_TIMEOUT_SEC）也有"同动作重复"检测，
        #   但两者都拦不住"每轮动作不同、DOM 偶有小变化却不推进"的长尾空转。
        #   此预算独立于 step4_case_timeout_sec（后者是 asyncio 硬超时，直接杀整用例丢证据），
        #   到点即主动收口返回，保留已获得的 DOM/证据，把 ERROR/超时转为有结论的 FAIL。
        _LOOP_BUDGET_SEC = int(
            getattr(pipeline_config, "step4_ai_loop_budget_sec", 120)
        )
        _loop_start_time = _time.time()

        # 解析导入提示
        if step_desc:
            import_hint, table_hint = self._parse_import_hint(step_desc)

        while ai_round < _effective_max_rounds and not ai_passed:
            ai_round += 1
            self._round_start_time = _time.time()  # 记录本轮开始时间

            # ★ Fix G(2026-09-27): AI loop 总墙钟预算熔断。
            #   根因: 单条用例 AI 兜底实测跑满 8 轮 6.4min（占全轮 69%），长尾拖垮整体
            #     执行效率；此处按总耗时主动收口，保留已获得证据（优于 asyncio 硬超时
            #     直接杀用例丢证据）。
            #   通用性: 纯墙钟预算，与业务/系统/页面无关。
            _loop_elapsed = _time.time() - _loop_start_time
            if _loop_elapsed > _LOOP_BUDGET_SEC:
                ai_error = (
                    f"AI loop 总预算 {_LOOP_BUDGET_SEC}s 已耗尽"
                    f"(已用 {int(_loop_elapsed)}s, round={ai_round}/{_effective_max_rounds})，提前终止"
                )
                _log.warning("    [AI] %s", ai_error)
                break

            # ★ v3.0.1 (2026-07-10): 1分钟无DOM动作致命bug检测
            _idle_elapsed = _time.time() - _last_dom_action_time
            if _idle_elapsed > _IDLE_TIMEOUT_SEC:
                _log.error(
                    "[FATAL] AI loop %ds 无成功DOM操作，判定为致命bug，中断用例执行 "
                    "(round=%d/%d, idle_timeout=%ds)",
                    int(_idle_elapsed),
                    ai_round,
                    _effective_max_rounds,
                    _IDLE_TIMEOUT_SEC,
                )
                ai_error = f"致命bug: AI loop {int(_idle_elapsed)}s 无DOM操作(超{_IDLE_TIMEOUT_SEC}s阈值)"
                break

            _log.info(
                "    [AI Round %d/%d] 分析页面并决策...",
                ai_round,
                _effective_max_rounds,
            )

            # ★ 每轮初始化 token 用量（0-token 路径无 AI 调用，用空 dict；AI 路径在 line 241 覆盖）
            _token_usage = {}

            # --- 获取 DOM ---
            dom_elements = await _get_dom_with_retry(engine, ai_round)
            if dom_elements is None:
                ai_error = f"获取 DOM 失败"
                break
            last_dom_elements = dom_elements  # ★ 缓存DOM元素列表，用于失败时的错误报告

            # --- SmartLocator 三层分流 ---
            # 关键词匹配在剪枝 DOM 上做 → 命中即把 prompt 缩到极短，未命中再喂全量
            # 不调任何 AI / embedding / RAG，纯本地规则
            smart_match = match_step_to_element(
                step_desc=step_desc,
                step_expected=step_expected,
                case_meta=case_meta,
                elements=dom_elements,
                actions=step.get("actions") if isinstance(step, dict) else None,
            )
            _log.info(
                "    [SmartLocator] %s | %s",
                smart_match.confidence.upper(),
                smart_match.debug_summary,
            )

            # 按置信度组装"DOM 片段" prompt 段
            if smart_match.confidence == "high":
                # ★ 阶段拆分(2026-07-25): SmartLocator high 置信 0-token 执行已移至
                #   _ai_loop_phases._execute_0token_smart_locator（§4.3 单文件≤800行）
                #   控制流: 用状态返回值替代 continue — "continue"(成功跳过AI) / "fallback_to_ai"(回退到AI)
                _0t_action, _0t_err, _0t_dom = await _execute_0token_smart_locator(
                    smart_match, step, step_desc, engine, _token_usage, self
                )
                if _0t_action == "continue":
                    step_passed = True
                    ai_passed = True
                    ai_error = ""
                    continue  # 跳过 AI 调用
                # fallback_to_ai: 设置 ai_error + last_dom，继续走 AI 路径
                ai_error = _0t_err
                last_dom = _0t_dom
            elif smart_match.confidence == "medium":
                # 给 AI Top-K 候选元素（仅元素本身，不带分数细节）
                top_k_elements = [c["element"] for c in smart_match.candidates]
                last_dom = json.dumps(top_k_elements, ensure_ascii=False)
                last_dom = (
                    f"【SmartLocator 候选元素 Top-{len(top_k_elements)}】\n{last_dom}\n"
                    f"请从候选元素中选择最匹配的一个并输出 action_type/action_params。"
                )
            else:
                # LOW：智能定位兜底——基于意图关键词精准筛选相关元素
                last_dom = _build_smart_locator_dom(
                    smart_match, dom_elements, case_meta, step, ai_round, self
                )

            # ★ 升级策略1+2：滚动查找 + 多入口查找
            last_dom = await _try_upgrade_strategies(
                self,
                engine,
                smart_match,
                dom_elements,
                last_dom,
                ai_round,
                case_meta,
                step,
                step_desc,
            )

            # --- 获取截图 ---
            _log.warning(
                "[_ai_loop] last_dom长度=%d, 准备获取截图",
                len(last_dom) if last_dom else 0,
            )
            try:
                screenshot = await engine.get_screenshot()
                last_screenshot = screenshot or ""
                _log.warning(
                    "[_ai_loop] 截图获取完成: %s", "有" if last_screenshot else "无"
                )
            except Exception as _e:
                _log.warning("[_ai_loop] 截图获取异常: %s", str(_e)[:200])
                _log.debug("[_ai_loop] 非关键异常: %s", _e, exc_info=True)
                last_screenshot = ""

            # --- 构建 AI Prompt ---
            ai_prompt = _build_ai_prompt(
                meta_text,
                import_hint,
                table_hint,
                ai_round,
                ai_error,
                step_desc,
                step_expected,
                step,
                engine,
                last_dom,
                last_screenshot,
            )
            # 清空上一轮错误，本轮会重新记录
            ai_error = ""

            # --- 调用 AI ---
            # ★ P1-8 单元B: op_cache_v2 查询（仅首轮，命中则跳过 AI 调用节省 token）
            ai_data, _cache_hit, _cache_repair_info = _query_op_cache_v2(
                ai_round, dom_elements, case_meta, step, engine
            )

            # ★ v10: 累计 cache 命中/未命中
            if _cache_hit:
                _cache_hit_count += 1
            else:
                _cache_miss_count += 1

            if not _cache_hit:
                _log.warning(
                    "[_ai_loop] 准备调用 decide_action, prompt长度=%d, screenshot=%s",
                    len(ai_prompt),
                    "有" if last_screenshot else "无",
                )
                try:
                    # ★ Phase 3 (2026-07-11): text-only 优先 — 先用纯文本 prompt 尝试，
                    #    仅当 AI 不确定时才回退到 vision API。每轮节省 3-5s 截图传输+vision推理。
                    _vision_used = False
                    ai_response = None
                    if last_screenshot and ai_round > 1:
                        # 首轮必须用 vision（页面状态未知），后续轮次可尝试 text-only
                        _log.info("    [AI] 尝试 text-only 模式...")
                        with guard("_run_ai_round_loop: _text_response = ai.decide_action(ai", log=_log):
                            _text_response = ai.decide_action(ai_prompt, screenshot="")
                            if _text_response:
                                _text_data = (
                                    _text_response
                                    if isinstance(_text_response, dict)
                                    else None
                                )
                                if not _text_data and isinstance(_text_response, str):
                                    try:
                                        _text_data = json.loads(_text_response.strip())
                                    except Exception as _se2:
                                        _log.debug(
                                            "[_ai_loop] 异常降级(pass): %s",
                                            str(_se2)[:60],
                                            exc_info=True,
                                        )
                                        pass
                                if _text_data:
                                    _at = _text_data.get("action_type", "")
                                    # 不确定的响应类型：fail/wait/refresh/空 → 需要 vision
                                    if _at and _at not in (
                                        "fail",
                                        "wait",
                                        "refresh",
                                        "",
                                    ):
                                        ai_response = _text_response
                                        _vision_used = False
                                        _log.info(
                                            "    [AI] text-only 成功: action_type=%s (跳过 vision)",
                                            _at,
                                        )
                                    else:
                                        _log.info(
                                            "    [AI] text-only 不确定(%s)，回退到 vision",
                                            _at or "empty",
                                        )

                    if _vision_used or not ai_response:
                        ai_response = ai.decide_action(
                            ai_prompt, screenshot=last_screenshot
                        )
                        _vision_used = True
                    # ★ 根因修复（2026-09-23）D17 —— 空响应即判"AI 无响应"= AI 稳定性缺口：
                    #   实测 ModSystem round3/round4：本地 qwen2.5:7b 间歇性返回 None
                    #   （4/96 ≈ 4%），用例被记为 ERROR("AI 无响应")；同一用例上一轮
                    #   FAIL/这轮 ERROR → 结论抖动（同输入不同输出），违背
                    #   "智能 = AI 判断 + AI 稳定性"。
                    #   修复：空响应先**有界重试**再定论（不掩盖真实不可用：
                    #   重试仍空才 break），并如实记日志。通用性：与业务/系统无关。
                    # ★ 2026-09-26 增强：重试次数从 1→2，增加指数退避。
                    #   理由：单次重试对间歇性故障覆盖率不足（4% 故障率下 1 次重试
                    #     仍有 0.16% 漏网；更高故障率场景下更明显）。2 次重试 + 退避
                    #     可将偶发空响应的影响降到最低，且总耗时增量可控（< 5s）。
                    _MAX_AI_EMPTY_RETRIES = 2
                    _retry_count = 0
                    # ★ 根因修复(2026-09-26) E1 —— 区分"确定性通道故障"与"瞬时空响应"：
                    #   实测 accept_sut_0923 试点 9/18 条被记 "AI 无响应"，真因是视觉通道
                    #   返回 HTTP 400 "Model does not exist"（模型名不存在），文本通道正常。
                    #   对确定性故障重试毫无意义（同请求同结果），只会白等 + 掩盖真因。
                    #   故：硬失败 → 跳过重试并如实上报真实错误（可归因、可运维）；
                    #       仅"无错误的空响应"才走有界重试。
                    _hard_err = str(getattr(ai, "_last_error", "") or "")
                    _is_hard = bool(getattr(ai, "_last_hard_error", False))
                    if _is_hard:
                        _log.warning(
                            "[_ai_loop] AI 确定性故障（跳过重试）: %s", _hard_err[:200]
                        )
                    # ★ 2026-10-27: 运行期 AI 闸门（唯一判据 runtime_ai_gate）关闭时，
                    #   decide_action 必然返回 None —— 这是**策略性拒绝**而非瞬时空响应，
                    #   重试同请求同结论。原实现每次都白等 1s+2s（实测单轮 187 次空响应
                    #   ≈ 9 分钟纯等待），并刷屏日志。与「硬失败不重试」同一判据形态。
                    from src.ai.runtime_ai_gate import runtime_ai_denied

                    _ai_denied = bool(runtime_ai_denied())
                    if _ai_denied and not ai_response:
                        _log.info(
                            "[_ai_loop] 运行期 AI 闸门关闭 → 跳过空响应重试（确定性拒绝）"
                        )
                    while (
                        not ai_response
                        and not _is_hard
                        and not _ai_denied
                        and _retry_count < _MAX_AI_EMPTY_RETRIES
                    ):
                        _retry_count += 1
                        # 指数退避：第 1 次 1s，第 2 次 2s
                        _delay = 2 ** (_retry_count - 1)
                        _log.warning(
                            "[_ai_loop] AI 第 %d 次空响应（decide_action 返回 None），%.1fs 后重试 %d/%d",
                            _retry_count,
                            _delay,
                            _retry_count,
                            _MAX_AI_EMPTY_RETRIES,
                        )
                        with guard("_run_ai_round_loop: import asyncio as _ai_retry_asyncio", log=_log):
                            import asyncio as _ai_retry_asyncio

                            await _ai_retry_asyncio.sleep(_delay)
                        with guard("_run_ai_round_loop: ai_response = ai.decide_action( ai_p", log=_log, level=logging.WARNING):
                            ai_response = ai.decide_action(
                                ai_prompt, screenshot=last_screenshot
                            )
                    _log.warning(
                        "[_ai_loop] decide_action 返回: type=%s, value=%s, vision=%s",
                        type(ai_response).__name__,
                        repr(ai_response)[:200] if ai_response else "None",
                        "yes" if _vision_used else "no",
                    )
                    if not ai_response:
                        # ★ E1(2026-09-26): 有真实错误 → 如实归因（可运维）；
                        #   纯空响应（无错误信息）才记"AI 无响应"。
                        _real = str(getattr(ai, "_last_error", "") or "")
                        if _real:
                            ai_error = f"AI 调用失败: {_real[:160]}"
                        else:
                            ai_error = f"AI 无响应(含{_MAX_AI_EMPTY_RETRIES}次重试)"
                        break
                except Exception as e:
                    _log.warning("[_ai_loop] decide_action 异常: %s", str(e)[:200])
                    _log.error("    [AI] 调用失败: %s", str(e)[:80])
                    ai_error = f"AI 调用失败: {str(e)[:80]}"
                    break

                # --- 解析 AI 响应 ---
                ai_data, _parse_err = _parse_ai_response(ai_response)
                # ★ F1 根因修复(2026-09-26)：AI 返回非法 JSON 不再直接判 ERROR。
                #   实测 accept_sut_0923 试点：TC-REQ-002-B01「Expecting value: line 1
                #   column 1」、TC-REQ-002-N01「Expecting ',' delimiter」—— 同用例
                #   上一轮可正常执行，属 AI **输出格式抖动**（既非产品缺陷也非引擎缺口），
                #   却被记 ERROR（置信度 0.13）→ 污染通过率与归因。
                #   修复：**有界重试**（≤2 次）+ 明确要求"只输出合法 JSON"；
                #   重试仍失败才如实上报（不掩盖真实不可用）。
                _MAX_JSON_RETRIES = 2
                _json_retry = 0
                while _parse_err and _json_retry < _MAX_JSON_RETRIES:
                    _json_retry += 1
                    _log.warning(
                        "[_ai_loop] AI 响应非合法 JSON(%s)，第 %d/%d 次重试并要求纯 JSON",
                        _parse_err[:80], _json_retry, _MAX_JSON_RETRIES,
                    )
                    try:
                        _fix_prompt = (
                            ai_prompt
                            + "\n\n★ 上一次响应无法解析为 JSON（%s）。"
                            "请**只输出一个合法 JSON 对象**：不要解释文字、"
                            "不要 markdown 代码块、不要在字符串值里使用裸换行。"
                            % _parse_err[:60]
                        )
                        ai_response = ai.decide_action(_fix_prompt, screenshot="")
                        ai_data, _parse_err = _parse_ai_response(ai_response)
                    except Exception as _je:
                        _log.warning("[_ai_loop] JSON 重试异常: %s", str(_je)[:120])
                        break
                if _parse_err:
                    ai_error = _parse_err
                    break

                # ★ Batch10: 跟踪本轮 AI 决策（供循环结束后缓存写入）
                _last_ai_action = ai_data
                _last_dom_for_cache = dom_elements

            action_type = ai_data.get("action_type", "")
            action_params = ai_data.get("action_params", {})
            reasoning = ai_data.get("reasoning", "")

            _log.info("    [AI] 决策: %s | 推理: %s", action_type, reasoning[:60])

            # ★ 连续无变化检测：同 action_type + 同 selector/value 重复出现则提前终止
            #   ★ F2(2026-09-26)：判定前**实测**页面是否发生变化（URL + 可见文本长度），
            #     确有变化 → 上一动作生效，重置重复计数（不再凭签名臆断"页面没有变化"）。
            _fp = await _page_fingerprint(engine._page)
            _page_changed = bool(_last_fingerprint) and _fp != _last_fingerprint
            # ★ Fix G(2026-09-27)+Fix B(2026-09-27): 连续无进展计数 —— 页面指纹连续不变
            #   仅**加上**"上一轮无成功 DOM 交互"才确认为空转。指纹不变但上一轮
            #   fill/click 成功派发（_last_dom_action_time 已更新）→ 不算无进展，
            #   避免把"合法操作但页面正文稳定"误杀（实测 002-UX02/008-UX02）。
            if (
                _last_fingerprint
                and _fp == _last_fingerprint
                and not _last_round_interacted
            ):
                _no_progress_count += 1
            else:
                _no_progress_count = 0
            _last_fingerprint = _fp
            _decision, _repeat_err, _new_state = _check_repeat_action(
                action_type,
                action_params,
                _last_action_signature,
                _no_change_count,
                _max_no_change_rounds,
                _no_change_reset_count,
                _MAX_RESET_COUNT,
                page_changed=_page_changed or _last_form_effective,
            )
            _no_change_count = _new_state["_no_change_count"]
            _last_action_signature = _new_state["_last_action_signature"]
            _no_change_reset_count = _new_state["_no_change_reset_count"]

            if _decision == "break":
                ai_error = _repeat_err
                break
            elif _decision == "reload_and_continue":
                # 未达阈值 → 刷新页面 + 提示换策略
                try:
                    await engine._page.reload(wait_until="load", timeout=15000)
                    try:
                        await engine._page.wait_for_load_state(
                            "networkidle", timeout=10000
                        )
                    except Exception as _e:
                        _log.debug("wait_for_load_state 超时: %s", _e, exc_info=True)
                    await asyncio.sleep(2)
                except Exception as _e:
                    _log.debug("page.reload 失败: %s", _e, exc_info=True)
                ai_error = _repeat_err
            elif _decision == "get_dom_and_continue":
                # 达到阈值但还有重置机会 → 获取实际 DOM 给 AI 最后机会
                _actual_dom = ""
                with guard("_run_ai_round_loop: _body_text = await engine._page.loca", log=_log):
                    _body_text = await engine._page.locator("body").inner_text(
                        timeout=3000
                    )
                    _actual_dom = (_body_text or "")[:2000]
                    _log.info(
                        "    [AI] 重复操作%d次，获取当前页面实际DOM(%d字符)",
                        _no_change_count + 1,
                        len(_actual_dom),
                    )
                if _actual_dom:
                    ai_error = (
                        f"你已连续{_no_change_count + 1}轮选择相同操作({action_type})，"
                        f"但页面没有变化。以下是当前页面的**实际可见文本**，"
                        f"请基于这些真实内容重新决策：\n---当前页面实际DOM---\n{_actual_dom}\n---"
                        f"请选择页面中实际存在的元素进行操作。"
                        f"（警告：这是你最后一次重置机会，再次重复相同操作将终止执行）"
                    )
                    _log.info(
                        "    [AI] 已重置 %d/%d 次",
                        _no_change_reset_count,
                        _MAX_RESET_COUNT,
                    )
                else:
                    ai_error = f"AI 连续 {_no_change_count + 1} 轮选择相同操作，无法推进当前步骤"
                    _log.warning("    [AI] %s", ai_error)
                    break

            # ★ Fix G(2026-09-27): 连续无进展早停 —— 页面指纹连续 N 轮完全不变，
            #   说明无论如何动作都无法推进当前步骤 → 立即终止，避免跑满 8 轮（长尾空转）。
            #   放在重复检测之后：可先用尽"重载/喂真实DOM"的既有恢复机制，再收口。
            if _no_progress_count >= _max_no_progress_rounds:
                ai_error = (
                    f"AI 连续 {_no_progress_count} 轮页面无变化(URL+可见文本未变)，"
                    f"判定无法推进，提前终止(节省执行时间)"
                )
                _log.warning("    [AI] %s", ai_error)
                break

            # ★ 捕获 AI token 用量（HarnessGovernancePlugin 追踪）
            _token_usage = getattr(ai, "_last_usage", None) or {}

            # 记录 Step 4 浏览器 AI 轮次指标到 ai_health_metrics
            _record_browser_ai_round(ai, self, case_meta, step_desc, ai_round)

            # --- 执行 AI 决策 ---
            # ★ Fix B(2026-09-27): 记录本回合起点，回合末据 _last_dom_action_time
            #   判定"本回合是否成功派发了 DOM 交互"（早停判据的交互维度）。
            _ts_round_start = _time.time()
            # ★ 归一化（2026-08-04）：查表优先 + elif 兜底（system_design Part A §4.2）。
            #   _try_library_fast_path 已泛化为「任意 action_id 先查表」；
            #   done/fail 为控制流动作，不走工具层；ai_id= 前缀在 fast path 内部回退 elif。
            #   library_dispatch 内部已通过 _execute_tool→run_action_with_hooks 内置 SelfHealing，
            #   命中后（无论成功/失败）不再走 elif，避免重复 SelfHealing。
            _pre_lib_url = engine._page.url if engine._page else ""
            if action_type in ("done", "fail"):
                _lib_handled = False
                _lib_ok = False
                _lib_err = ""
            else:
                _lib_handled, _lib_ok, _lib_err = await self._try_library_fast_path(
                    engine, action_type, action_params
                )

            if _lib_handled:
                # ★ v3.0.1: library fast path 成功时更新无动作时间戳
                if _lib_ok:
                    _last_dom_action_time = _time.time()
                    # ★ 通用修复 (2026-07-17): SPA navigate 后 URL 可能不变，
                    #   重置连续无变化检测状态，避免 AI 误判为重复操作
                    if action_type == "navigate":
                        _post_lib_url = engine._page.url if engine._page else ""
                        if (
                            _pre_lib_url
                            and _post_lib_url
                            and _pre_lib_url == _post_lib_url
                        ):
                            _log.info(
                                "    [AI] SPA navigate (lib): URL 未变化(%s)，重置循环检测状态",
                                _post_lib_url[:80],
                            )
                            _no_change_count = 0
                            _last_action_signature = ""
                    # ★ 通用化: library fast path 成功时也反哺 applicable_tags
                    if action_type in ("click", "fill"):
                        _fast_sel = action_params.get("selector", "")
                        _el_tag = _find_element_tag_by_selector(dom_elements, _fast_sel)
                        _enrich_action_tags(action_type, _el_tag)
                        # ★ Phase 2: target_hint 自修正 — library fast path 成功
                        _el_info = next(
                            (e for e in dom_elements if e.get("selector") == _fast_sel),
                            None,
                        )
                        if _el_info:
                            self._enrich_page_explorer(step, _el_info, engine)
                else:
                    # ★ 通用修复 (2026-07-17): library_dispatch 失败时回退到 AI 循环，
                    #   而非立即退出。library 内部已做 SelfHealing，但 AI 循环可尝试
                    #   不同定位策略（如 ai_id= 格式），避免因单次定位失败直接终止。
                    #   设置 _lib_handled=False 让代码继续进入 elif 分支。
                    _log.warning(
                        "    [AI] library_dispatch 失败，回退到 AI 循环: %s → %s",
                        action_type,
                        _lib_err[:150],
                    )
                    _lib_handled = False
                # library_dispatch 已处理（成功），跳过 SelfHealing；失败则回退到 elif
            elif action_type == "click":
                with guard("_run_ai_round_loop: selector = action_params.get('select", log=_log, level=logging.WARNING):
                    selector = action_params.get("selector", "")
                    if selector:

                        async def _do_click(a):
                            # ★ 自愈后的 hint：ai_id= → 快速路径（Playwright 原生 → _locate_by_hint 兜底）
                            healed = a.get("target_hint", selector)
                            # ai_id= → data-ai-id 属性选择器（快速路径，_locate_by_hint 也支持但直接构建更快）
                            if healed.startswith("ai_id="):
                                await engine._page.click(
                                    '[data-ai-id="%s"]'
                                    % _escape_css_str(healed[6:].strip("\"'")),
                                    timeout=5000,
                                )
                                return
                            # ★ 通用点击：先尝试直接点击，失败后父元素回退 + force click
                            _clicked = False
                            _last_err = None
                            # 策略1：直接点击
                            try:
                                await engine._page.click(healed, timeout=5000)
                                _clicked = True
                            except Exception as _e1:
                                _last_err = _e1
                                _log.debug(
                                    "[_ai_loop] _do_click 直接点击失败: %s",
                                    str(_e1)[:80],
                                )
                                # 策略2：_locate_by_hint 定位后点击
                                locator = await _locate_by_hint(engine._page, healed)
                                if locator:
                                    try:
                                        await locator.click(timeout=5000)
                                        _clicked = True
                                    except Exception as _e2:
                                        _last_err = _e2
                                        _log.debug(
                                            "[_ai_loop] _do_click locator点击失败: %s",
                                            str(_e2)[:80],
                                        )
                                        # 策略3：父元素回退（最多3层）
                                        with guard("_do_click: _cur = locator", log=_log):
                                            _cur = locator
                                            for _depth in range(3):
                                                _parent = _cur.locator("..")
                                                if await _parent.count() > 0:
                                                    _log.info(
                                                        "[_ai_loop] 尝试父元素 depth=%d",
                                                        _depth + 1,
                                                    )
                                                    await _parent.click(timeout=5000)
                                                    _clicked = True
                                                    _log.info(
                                                        "[_ai_loop] ✓ 父元素点击成功 (depth=%d)",
                                                        _depth + 1,
                                                    )
                                                    break
                                                _cur = _parent
                                        # 策略4：force click 兜底
                                        if not _clicked and locator:
                                            with guard("_do_click: await locator.click( timeout=5000, f", log=_log):
                                                await locator.click(
                                                    timeout=5000, force=True
                                                )
                                                _clicked = True
                                                _log.info(
                                                    "[_ai_loop] ✓ force click 成功"
                                                )
                                if not _clicked:
                                    raise ValueError(
                                        f"点击失败: {healed} — {str(_last_err)[:80]}"
                                        if _last_err
                                        else f"未找到元素: {healed}"
                                    )

                        # ★ 把 reasoning 传给 run_action_with_hooks，统一由 SelfHealing 做 #ai-N 失效兜底
                        result = await run_action_with_hooks(
                            engine,
                            "click",
                            selector,
                            do_action=_do_click,
                            action_extra={"reasoning": reasoning},
                            token_usage=_token_usage,
                        )
                        if result.get("success"):
                            # ★ v3.0.1: 更新无动作时间戳
                            #   页面就绪等待已收敛到 run_action_with_hooks →
                            #   settle_page_after_action，此处不再重复等待
                            _last_dom_action_time = _time.time()
                            # ★ 通用化: AI兜底成功 → 反哺 applicable_tags 到操作库
                            _el_tag = _find_element_tag_by_selector(
                                dom_elements, selector
                            )
                            _enrich_action_tags(action_type, _el_tag)
                            # ★ Phase 2: target_hint 自修正 — AI click 成功
                            _el_info = next(
                                (
                                    e
                                    for e in dom_elements
                                    if e.get("selector") == selector
                                ),
                                None,
                            )
                            if _el_info:
                                self._enrich_page_explorer(step, _el_info, engine)
                        else:
                            _log.warning(
                                "    [AI] 点击失败: %s", result.get("error", "?")
                            )

            elif action_type == "fill":
                with guard("_run_ai_round_loop: selector = action_params.get('select", log=_log, level=logging.WARNING):
                    selector = action_params.get("selector", "")
                    value = action_params.get("value", "")
                    if selector:

                        async def _do_fill(a):
                            # ★ 自愈后的 hint：先处理 ai_id= → Playwright 原生 → _locate_by_hint 兜底
                            healed = a.get("target_hint", selector)
                            healed_text = a.get("text", value)
                            # ai_id= → data-ai-id 属性选择器（快速路径，_locate_by_hint 兜底也支持）
                            if healed.startswith("ai_id="):
                                await engine._page.fill(
                                    '[data-ai-id="%s"]'
                                    % _escape_css_str(healed[6:].strip("\"'")),
                                    healed_text,
                                    timeout=5000,
                                )
                                return
                            try:
                                await engine._page.fill(
                                    healed, healed_text, timeout=5000
                                )
                            except Exception as _e:
                                _log.debug(
                                    "[_ai_loop] _do_fill 异常: %s", _e, exc_info=True
                                )
                                locator = await _locate_by_hint(engine._page, healed)
                                if not locator:
                                    raise ValueError(f"未找到元素: {healed}")
                                await locator.fill(healed_text, timeout=5000)

                        # ★ 把 reasoning 传给 run_action_with_hooks，统一由 SelfHealing 做 #ai-N 失效兜底
                        result = await run_action_with_hooks(
                            engine,
                            "fill",
                            selector,
                            do_action=_do_fill,
                            action_extra={"text": value, "reasoning": reasoning},
                            token_usage=_token_usage,
                        )
                        if result.get("success"):
                            # ★ v3.0.1: 更新无动作时间戳
                            #   （就绪等待见 settle_page_after_action，此处不重复）
                            _last_dom_action_time = _time.time()
                            # ★ 通用化: AI兜底成功 → 反哺 applicable_tags 到操作库
                            _el_tag = _find_element_tag_by_selector(
                                dom_elements, selector
                            )
                            _enrich_action_tags(action_type, _el_tag)
                            # ★ Phase 2: target_hint 自修正 — AI fill 成功
                            _el_info = next(
                                (
                                    e
                                    for e in dom_elements
                                    if e.get("selector") == selector
                                ),
                                None,
                            )
                            if _el_info:
                                self._enrich_page_explorer(step, _el_info, engine)
                        else:
                            _log.warning(
                                "    [AI] 填充失败: %s", result.get("error", "?")
                            )

            elif action_type == "navigate":
                with guard("_run_ai_round_loop: url = action_params.get('url', '')", log=_log, level=logging.WARNING):
                    url = action_params.get("url", "")
                    _hint = action_params.get("target_hint", "")

                    # ★ 通用修复 (2026-07-18 v2): URL 为空时，直接用 target_hint 走菜单导航
                    #   当 Step3 AI 生成的 URL 被 dispatcher 过滤后（中文文本等），url 为空。
                    #   此时不应跳过导航，而是直接使用 target_hint 走菜单导航。
                    if not url and _hint:
                        _log.info(
                            "    [AI] URL为空，使用 target_hint 菜单导航: %s",
                            _hint[:80],
                        )
                        try:
                            from src.pipeline.steps.step4.browser_tools.execution._single import (
                                _fallback_menu_navigate,
                            )

                            _menu_nav_ok = await _fallback_menu_navigate(
                                engine, _hint, _hint
                            )
                            if _menu_nav_ok and engine._page:
                                _captured_url = engine._page.url or ""
                                _log.info(
                                    "    [AI] 菜单导航成功(hint), 捕获URL: %s",
                                    _captured_url[:120],
                                )
                                action_params["url"] = _captured_url
                                action_params["_captured_url"] = _captured_url
                                result = {"success": True, "error": ""}
                                _last_dom_action_time = _time.time()
                                try:
                                    await engine._page.wait_for_load_state(
                                        "domcontentloaded", timeout=10000
                                    )
                                except Exception:
                                    await engine._page.wait_for_timeout(500)
                                continue
                            else:
                                _log.warning(
                                    "    [AI] 菜单导航失败(hint): %s", _hint[:80]
                                )
                                result = {
                                    "success": False,
                                    "error": f"菜单导航失败: {_hint}",
                                }
                                continue
                        except Exception as _me:
                            _log.warning(
                                "    [AI] 菜单导航异常(hint): %s", str(_me)[:80]
                            )
                            result = {
                                "success": False,
                                "error": f"菜单导航异常: {str(_me)[:80]}",
                            }
                            continue

                    if url:
                        # ★ 通用修复 (2026-07-18): 剥离查询参数中的占位符值
                        # AI 编造的占位符参数值（如 yzCode=YZCODE.000, blendCode=BLEND_000）
                        # 会导致目标系统返回空白/错误页面，需剥离后仅保留合法的路由参数
                        import re as _placeholder_re

                        if "?" in url:
                            _path_part, _query_part = url.split("?", 1)
                            _params = _query_part.split("&")
                            _clean_params = []
                            _stripped = []
                            for _p in _params:
                                if "=" in _p:
                                    _k, _v = _p.split("=", 1)
                                    _is_ph = False
                                    _v_upper = _v.upper()
                                    if _v_upper.endswith(".000") or _v_upper.endswith(
                                        "_000"
                                    ):
                                        _is_ph = True
                                    elif _placeholder_re.match(
                                        r"^[A-Z_]+\.[0-9]+$", _v_upper
                                    ) or _placeholder_re.match(
                                        r"^[A-Z_]+_[0-9]+$", _v_upper
                                    ):
                                        _is_ph = True
                                    elif (
                                        _placeholder_re.match(r"^[0-9]+$", _v)
                                        and len(_v) <= 5
                                    ):
                                        _is_ph = True
                                    if _is_ph:
                                        _stripped.append(f"{_k}={_v}")
                                    else:
                                        _clean_params.append(_p)
                                else:
                                    _clean_params.append(_p)
                            if _stripped:
                                _clean_query = (
                                    "&".join(_clean_params) if _clean_params else ""
                                )
                                url = _path_part + (
                                    "?" + _clean_query if _clean_query else ""
                                )
                                _log.info(
                                    "    [AI] 剥离占位符后URL: %s (剥离 %d: %s)",
                                    url,
                                    len(_stripped),
                                    _stripped,
                                )
                                action_params["url"] = url
                        # ★ 通用修复 (2026-07-18): 非绝对URL先尝试菜单导航，
                        #   利用导航地图的 parent_path 逐级点击菜单项，
                        #   导航成功后捕获真实URL。失败才回退到直接URL导航。
                        _is_absolute = url.startswith("http://") or url.startswith(
                            "https://"
                        )
                        _menu_nav_ok = False
                        _captured_url = ""
                        if not _is_absolute:
                            _log.info("    [AI] 非绝对URL，尝试菜单导航: %s", url[:80])
                            with guard("_run_ai_round_loop: from src.pipeline.steps.step4.browse", log=_log, level=logging.WARNING):
                                from src.pipeline.steps.step4.browser_tools.execution._single import (
                                    _fallback_menu_navigate,
                                )

                                _hint = action_params.get("target_hint", url)
                                _menu_nav_ok = await _fallback_menu_navigate(
                                    engine, url, _hint
                                )
                                if _menu_nav_ok and engine._page:
                                    _captured_url = engine._page.url or ""
                                    _log.info(
                                        "    [AI] 菜单导航成功，捕获URL: %s",
                                        _captured_url[:120],
                                    )
                                    # ★ 将捕获的真实URL写入action_params，供后续使用
                                    action_params["url"] = _captured_url
                                    action_params["_captured_url"] = _captured_url

                        if _menu_nav_ok:
                            # 菜单导航成功，跳过 engine.goto
                            result = {"success": True, "error": ""}
                            _last_dom_action_time = _time.time()
                            try:
                                await engine._page.wait_for_load_state(
                                    "domcontentloaded", timeout=10000
                                )
                            except Exception:
                                await engine._page.wait_for_timeout(500)
                        elif _is_absolute:
                            # 绝对URL（已解析），走直接URL导航
                            _pre_nav_url = engine._page.url if engine._page else ""

                            async def _do_nav(a):
                                return await engine.goto(a.get("url", url))

                            result = await run_action_with_hooks(
                                engine,
                                "navigate",
                                url,
                                do_action=_do_nav,
                                action_extra={"url": url},
                                token_usage=_token_usage,
                            )
                            if result.get("success"):
                                _last_dom_action_time = _time.time()
                                try:
                                    await engine._page.wait_for_load_state(
                                        "domcontentloaded", timeout=10000
                                    )
                                except Exception:
                                    await engine._page.wait_for_timeout(500)
                                _post_nav_url = engine._page.url if engine._page else ""
                                if (
                                    _pre_nav_url
                                    and _post_nav_url
                                    and _pre_nav_url == _post_nav_url
                                ):
                                    _log.info(
                                        "    [AI] SPA navigate: URL 未变化(%s)，重置循环检测状态",
                                        _post_nav_url[:80],
                                    )
                                    _no_change_count = 0
                                    _last_action_signature = ""
                            else:
                                _log.warning(
                                    "    [AI] 导航失败: %s", result.get("error", "?")
                                )
                        else:
                            # ★ 通用修复 (2026-07-18 v2): 非绝对URL菜单导航失败后，回退到直接URL导航
                            #   goto() 已支持相对URL→绝对URL解析、占位符剥离、ERR_ABORTED处理。
                            #   直接URL导航失败再由 goto() 返回失败，不浪费AI轮次。
                            # ★ 通用修复 (2026-07-18 v3): /dashboard 是某SPA系统死页面，
                            #   菜单导航已失败，直接URL导航到 /dashboard 只会浪费AI轮次。
                            #   跳过 /dashboard 直接URL导航，将失败信息反馈给AI。
                            _url_lower = url.lower().strip().rstrip("/")
                            if (
                                _url_lower.endswith("/dashboard")
                                or _url_lower == "dashboard"
                            ):
                                _log.warning(
                                    "    [AI] 跳过死页面 /dashboard 直接URL导航，反馈AI重试"
                                )
                                result = {
                                    "success": False,
                                    "error": "/dashboard 是死页面，菜单导航失败，请尝试其他页面",
                                }
                            else:
                                _log.info(
                                    "    [AI] 菜单导航失败，回退到直接URL导航: %s",
                                    url[:80],
                                )
                                _pre_nav_url = engine._page.url if engine._page else ""

                                async def _do_nav_fallback(a):
                                    return await engine.goto(a.get("url", url))

                                result = await run_action_with_hooks(
                                    engine,
                                    "navigate",
                                    url,
                                    do_action=_do_nav_fallback,
                                    action_extra={"url": url},
                                    token_usage=_token_usage,
                                )
                                if result.get("success"):
                                    _last_dom_action_time = _time.time()
                                    try:
                                        await engine._page.wait_for_load_state(
                                            "domcontentloaded", timeout=10000
                                        )
                                    except Exception:
                                        await engine._page.wait_for_timeout(500)
                                    _post_nav_url = (
                                        engine._page.url if engine._page else ""
                                    )
                                    if (
                                        _pre_nav_url
                                        and _post_nav_url
                                        and _pre_nav_url == _post_nav_url
                                    ):
                                        _log.info(
                                            "    [AI] SPA navigate: URL 未变化(%s)，重置循环检测状态",
                                            _post_nav_url[:80],
                                        )
                                        _no_change_count = 0
                                        _last_action_signature = ""
                                else:
                                    _log.warning(
                                        "    [AI] 直接URL导航也失败: %s | %s",
                                        url[:80],
                                        result.get("error", "?"),
                                    )

            elif action_type == "assert":
                try:
                    assert_type = action_params.get("assert_type", "")
                    assert_selector = (action_params.get("selector", "") or "").strip()
                    assert_expected = action_params.get("expected", "")
                    # ★ 通用修复 (2026-07-18): 空选择器防护 — 空CSS选择器会导致 Playwright
                    #   locator("") 抛出 "Unexpected token" 解析错误。AI 可能因页面错误
                    #   生成空选择器，必须拦截而非崩溃。
                    if not assert_selector:
                        ai_error = "断言缺少selector: AI未生成有效CSS选择器，可能因当前页面无法定位目标元素"
                    elif assert_type == "element_exists":
                        count = await engine._page.locator(assert_selector).count()
                        if count > 0:
                            ai_passed = True
                        else:
                            ai_error = f"元素不存在: {assert_selector}"
                    elif assert_type == "text_contains":
                        text = (
                            await engine._page.locator(assert_selector).text_content()
                            or ""
                        )
                        if assert_expected in text:
                            ai_passed = True
                        else:
                            ai_error = f"文本不匹配: 期望 '{assert_expected}', 实际 '{text[:50]}'"
                    else:
                        # ★ fail-closed（2026-08-04）：未知断言类型 → FAIL，禁止静默 PASS
                        #   （原 `ai_passed = True` 是 assert 假 PASS 直接来源）。
                        ai_error = f"未知断言类型: {assert_type}"
                        _log.warning(
                            "    [AI] 未知断言类型: %s → 判定 FAIL（fail-closed）",
                            assert_type,
                        )
                except Exception as e:
                    ai_error = f"断言异常: {str(e)[:80]}"

            elif action_type == "done":
                ai_passed = True

            elif action_type == "fail":
                # ★ 通用修复 (2026-07-17): AI 判定失败时，先尝试菜单导航回退，
                #   而非直接退出。AI 可能因当前页面找不到元素而判定 fail，
                #   但目标元素可能存在于其他页面（通过菜单导航可达）。
                ai_error = action_params.get("reason", "AI 判定失败")
                _log.warning(
                    "    [AI] AI 判定失败: %s，尝试菜单导航回退", ai_error[:80]
                )
                _fallback_ok = await self._try_menu_fallback_for_fail(
                    engine, step, action_params
                )
                if _fallback_ok:
                    _log.info("    [AI] 菜单导航回退成功，继续 AI 循环")
                    ai_error = ""
                    continue
                _log.warning("    [AI] 菜单导航回退失败，退出 AI 循环")
                break

            else:
                _log.warning("    [AI] 未知动作类型: %s", action_type)

            # ★ 快速失败检测：结构化全部失败时，追踪连续同元素失败
            if structured_all_failed and not ai_passed:
                _cur_target = action_params.get("target_hint", "") or action_params.get(
                    "selector", ""
                )
                _prev_target = getattr(self, "_fast_fail_target", "")
                if _cur_target and _cur_target == _prev_target:
                    _consecutive_element_fail += 1
                else:
                    _consecutive_element_fail = 1
                    self._fast_fail_target = _cur_target
                if _consecutive_element_fail >= 3:
                    _log.warning(
                        "    [FastFail] 连续 %d 轮同元素(%s)失败，终止AI循环",
                        _consecutive_element_fail,
                        _cur_target[:50],
                    )
                    ai_error = f"快速失败: 连续{_consecutive_element_fail}轮无法定位'{_cur_target[:50]}'"
                    break

            # --- 等待页面稳定 ---
            # ★ Fix B(2026-09-27): 回合末回填"本回合是否成功派发 DOM 交互"，
            #   供下一轮顶部指纹判据使用（_last_dom_action_time 由各成功分支更新）。
            _last_round_interacted = _last_dom_action_time >= _ts_round_start
            # fill 幂等判据（**单一生成点**，覆盖 AI 直执行与查表快路径）：
            # 以"目标字段值==期望值"作为动作是否生效的可验证证据，与页面是否重绘无关。
            _last_form_effective = bool(action_type == "fill") and await _verify_form_value(
                engine,
                action_params.get("selector", ""),
                action_params.get("value", ""),
            )
            if not ai_passed:
                # ★ S4-011: 确定性等待替代 sleep — 等待页面异步操作完成
                try:
                    await engine._page.wait_for_load_state(
                        "domcontentloaded", timeout=2000
                    )
                except Exception:
                    await engine._page.wait_for_timeout(300)

        # --- AI 循环结束 ---
        if not ai_passed:
            step_passed = False
            step_error = ai_error or f"AI 未能在 {_effective_max_rounds} 轮内完成"
            # ★ P1-8 修复(2026-07-25): repair 拍 — 命中缓存但执行失败时删除缓存
            #   根因: 命中后失败不删缓存 → 下次继续命中坏缓存 → 重复失败
            #   通用性: 仅当 _cache_hit=True 且有 repair_info 时触发；删除失败非阻断
            #   覆盖性: 所有命中后失败路径都走此分支（ai_passed=False）
            #   自我反驳: 误删风险？→ 命中后失败说明缓存对应的 action 在当前页面无法执行，
            #           删缓存让下次重新学习是正确行为，非误删
            if _cache_hit and _cache_repair_info:
                with guard("_run_ai_round_loop: from src.pipeline.steps.step4.op_cac", log=_log):
                    from src.pipeline.steps.step4.op_cache.v2_store import get_v2_store

                    _rp, _rv, _rh = _cache_repair_info
                    get_v2_store().delete(_rp, _rv, _rh)
                    _log.info(
                        "    [OpCacheV2] repair: 命中缓存但执行失败，已删除缓存 (hash=%s...)",
                        str(_rh)[:12],
                    )
            # ★ 通用修复 (2026-07-18): 当因元素未找到失败时，附加页面可用元素列表
            #   用于失败报告，帮助开发者理解为什么元素不存在
            #   注意：这是诊断信息，不是让AI找替代元素
            _elem_not_found = any(
                kw in (step_error or "")
                for kw in [
                    "无法定位",
                    "找不到",
                    "SmartLocator",
                    "click failed",
                    "fill failed",
                    "no selector",
                    "元素不存在",
                ]
            )
            if _elem_not_found and last_dom_elements:
                _available = _extract_available_elements(last_dom_elements)
                if _available:
                    step_error = f"{step_error} | 页面可用元素: {_available.replace(chr(10), '; ')}"
            all_passed = False
        else:
            # ★ P1-8 单元C: op_cache_v2 写入（record：成功后写 action_sequence）
            if not _cache_hit:
                _record_op_cache_v2(
                    _last_ai_action, _last_dom_for_cache, case_meta, step, engine
                )
            # ★ 指标: 记录 AI loop 兜底成功
            _record_ai_loop_pass(case_meta)

        # ★ v10: 将 cache 计数写入 self，供 _base.py 消费
        self._ai_loop_cache_hits = _cache_hit_count
        self._ai_loop_cache_misses = _cache_miss_count
        if _cache_hit_count > 0 or _cache_miss_count > 0:
            _log.info("[_ai_loop] ★ v10: cache 计数: hits=%d misses=%d (标准step loop路径)",
                       _cache_hit_count, _cache_miss_count)

        return step_passed, step_error, all_passed
