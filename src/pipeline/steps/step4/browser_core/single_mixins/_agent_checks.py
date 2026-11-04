# -*- coding: utf-8 -*-
"""AgentExplorer 路径 mixin —— 从 _base.py 拆出（§4.3 单文件≤800行）。

职责（零业务逻辑变更）：
- _try_agent_explorer_path: AgentExplorer 实时探索主路径（P0-refactor, §16.2），
  含 record_execution 落库 / checks 验证 / 置信度闸门；无 _explore_task 时返回 None。
- _run_checks_for_agent: Agent 探索成功后，逐条执行 intent 中 checks 的客观验证，
  返回 {all_passed, summary, ui/api/db 三轨 evidence}。

组合位置：browser_core/single.py 的 _BrowserCoreSingleMixin 多继承加入本 mixin；
调用方（_base._execute_one_intent）经 self._try_agent_explorer_path /
self._run_checks_for_agent(...) 调用，签名不变。
"""

import logging
from src.core.guarded import guard

from src.core.config_loader import config
from ._base_classify import _safe_int
from ._assertion_verdict import (
    declares_executable_assertion,
    has_executed_assertion_verdict,
    has_no_negative_verdict,
    has_only_engine_gap_verdict,
)

_log = logging.getLogger(__name__)

# ★ 根因修复(2026-09-24) F7b —— "平凡检查"的判据是**选择器**，不是类型本身。
#   根因: 原实现用 `_TRIVIAL_CHECK_TYPES = {element_exists, no_error}` 按类型一刀切，
#     但 element_exists 承载两种截然不同的语义：
#     ① 整页兜底选择器（main / [role=main] / .content / #content / body）
#        → 任意正常页面恒真，确属平凡；
#     ② 具体选择器（错误提示 [role=alert]/.error、CSRF token input[name=...]、
#        搜索结果列表 [role=table]、XSS 提示等）
#        → 只在满足条件时才命中，是**真实断言**，属实质证据。
#     混淆二者的后果: 生成端已发具体选择器（F7 修复），执行端仍按"平凡"降级
#       → 真实通过的断言被误判证据不足 → PASS 被降级 SKIP(ASSERTION_UNVERIFIED)
#       → 系统性低估通过率（与"假 PASS"相反的另一种失真）。
#   通用判据: 选择器是否仅为通用页面容器（零业务硬编码，纯选择器形态判定）。
_GENERIC_PAGE_SELECTORS = {
    "main", "[role='main']", '[role="main"]', ".content", "#content", "body",
}


def _is_trivial_check(r: dict) -> bool:
    """该断言结果是否属"平凡检查"（不构成实质证据）。

    no_error 恒为平凡（"无错误提示元素"在正常页面为真）；
    element_exists 仅当其选择器退化为通用页面容器时才平凡。
    """
    _t = str(r.get("type", "") or "")
    if _t == "no_error":
        return True
    if _t != "element_exists":
        return False
    _sel = str(r.get("selector", "") or "").strip()
    if not _sel:
        return True
    _tokens = [x.strip().lower() for x in _sel.split(",") if x.strip()]
    if not _tokens:
        return True
    return all(tok in _GENERIC_PAGE_SELECTORS for tok in _tokens)


def _classify_checks_failure(verify_result, engine) -> tuple:
    """分类「探索成功后业务 checks 验证失败」的归属（通用规则，跨系统/行业）。

    探索成功 → 页面已渲染 → checks 断言失败 = 被测系统实际行为不满足需求/预期
    → PRODUCT_BUG(FAIL)。仅当引擎能力缺失（UNSUPPORTED_CHECK / 断言分派异常）
    → HARNESS_ERROR(ERROR)；网络/超时 → ENVIRONMENT_ERROR(ERROR)。

    ★ 根因修复（2026-09-23）D8 —— 产品缺陷被误记为引擎错误：
      旧实现只要 checks 里**出现任一** UNSUPPORTED_CHECK，整个用例即归
      HARNESS_ERROR(ERROR)。实测 ModSystem round2：TC-REQ-001-CP01 的
      viewport_rendering_check 已真实执行并以具体度量失败
      （mobile overlap=44px / a11y_violations=10），却因同批另有未支持 check
      而被记为 HARNESS_ERROR → 报告把真实产品缺陷（移动端布局重叠、点击目标
      不达标、a11y 违规）归入"执行异常"，系统性低估产品缺陷；更严重的是与同一行
      记录里引擎自身 AI 根因分析（ai_root_cause=PRODUCT_BUG）自相矛盾。
      修复：按"真正执行过的断言"归类 —— 只要存在**已执行且失败**的 check，
      即为产品行为不满足 → PRODUCT_BUG(FAIL)；仅当失败完全由未支持 check 造成
      （无任何已执行失败项）才判引擎侧 HARNESS_ERROR。

    返回: (failure_category, status)，与 failure_classifier.classify_failure 契约一致。
    """
    _checks = (verify_result or {}).get("checks") or []

    # 判定语义一律读结构化权威 `_track_protocol`，不本地维护 reason 白名单：
    # 白名单只看 reason、漏了 skipped/status，会把引擎自述"不适用"的 ⊘ 当成失败。
    from src.pipeline.steps.step4.verification._track_protocol import (
        is_negative_verdict as _is_negative_verdict,
    )

    _executed_failed = [
        c for c in _checks if isinstance(c, dict) and _is_negative_verdict(c)
    ]
    _unsupported = [
        c
        for c in _checks
        if isinstance(c, dict) and c.get("reason") == "UNSUPPORTED_CHECK"
    ]

    _summary = str((verify_result or {}).get("summary") or "")
    if "分派异常" in _summary or "断言分派异常" in _summary:
        return "HARNESS_ERROR", "ERROR"
    _low = _summary.lower()
    if "timeout" in _low or "timed out" in _low:
        return "ENVIRONMENT_ERROR", "ERROR"
    if engine is not None and getattr(engine, "_last_network_error", False):
        return "ENVIRONMENT_ERROR", "ERROR"
    # ★ D8: 已执行断言真实失败 → 产品行为不满足，优先于"存在未支持 check"
    # ★ D18（2026-09-23，同源共享谓词）—— 原判据只看 `checks` 里的 reason 白名单，
    #   漏掉两类引擎侧失败被兜底成 PRODUCT_BUG（实测 round5 共 19 条假 Bug）：
    #     · 三轨 track 级失败（如 API 轨"声明了API校验点但无API调用发生"）不在
    #       `checks` 列表里 → 落到函数末尾 `return PRODUCT_BUG`；
    #     · 执行器异常（如 `element_exists:✗(查询异常: Unexpected token ":")`）
    #       无 reason 字段 → 被 `_executed_failed` 当成"已执行失败"。
    #   修复：先由共享单点谓词定夺——存在产品不满足证据 → PRODUCT_BUG/FAIL；
    #   失败项全为引擎不可评估 → SKIP(ENGINE_COVERAGE_GAP)（防假 Bug，也不假绿）；
    #   两者都不成立才回落原有白名单兜底逻辑（保持既有语义不变）。
    if has_executed_assertion_verdict([verify_result or {}]):
        return "PRODUCT_BUG", "FAIL"
    if has_only_engine_gap_verdict([verify_result or {}]):
        return "ENGINE_COVERAGE_GAP", "SKIP"
    # 断言均标注"非判定态"（⊘ N/A 跳过/未验证）且无真实负向判定 ⇒ 无产品缺陷证据，
    # 判 SKIP：既不记假 Bug，也不记假绿。
    if has_no_negative_verdict([verify_result or {}]):
        return "ENGINE_COVERAGE_GAP", "SKIP"
    if _executed_failed:
        return "PRODUCT_BUG", "FAIL"
    # 仅当失败完全由引擎能力缺失造成时才归引擎侧
    if _unsupported:
        return "HARNESS_ERROR", "ERROR"
    return "PRODUCT_BUG", "FAIL"


def _finalize_agent_failure(failure_cat: str, status: str, agent_result) -> tuple:
    """对 classify_failure 结果做语义覆盖（单一事实源，跨系统/行业/页面）。

    权威需求未实现（_req_unimplemented=True）→ 需求已定义(is_implicit=0)但系统
    未实现/不可达 = 产品缺陷缺口 → PRODUCT_BUG(FAIL)。

    背景：此类失败由 _agent_executor 以单步 assert 断言
    直接返回，失败链路仅 1 步 → classify_failure 按"策略数<2 且 DOM 状态未知"
    误判为 HARNESS_ERROR(ERROR)。需求权威性语义与失败链路形态无关，必须覆盖之，
    否则"需求未实现"这一最典型的产品缺陷会被记成引擎侧错误。

    ★ 补充根因修复（2026-09-23）D8b —— 同类误判在「结构化断言失败」上复发：
      实测 ModSystem TC-REQ-001-CP01/UX01（移动端重叠 44px、点击目标不达标、
      a11y 违规）由**已真实执行**的 viewport_rendering_check /
      click_target_size_check 产出具体度量后判定失败，但失败链路同样只有 1 步
      （strategy=execute, dom_has_element=None）→ classify_failure 再次误判
      HARNESS_ERROR，把真实产品缺陷记成引擎错误；且与同一行 details 里引擎自身
      AI 根因分析 ai_root_cause=PRODUCT_BUG 自相矛盾。
      修复：只要 step_results 中存在**已执行并给出否定结论**的断言证据
      （_verify_result.passed is False，或步骤错误含"结构化校验失败"），即为
      产品行为不满足 → PRODUCT_BUG(FAIL)，与失败链路长度无关。
      通用性：不依赖业务字段/模块名，仅以"断言是否真实执行并给出结论"为判据。

    ★ 同日收窄 —— D8b 初版判据过宽会让"动作失败"升格为产品缺陷（假 Bug）：
      实测 TC-REQ-001-E01（Agent 点不到按钮）也满足 _verify_result.passed is False，
      会被错误升格。现改由 _has_executed_assertion_verdict 判定，要求出现
      具名断言 + 否定标记（✗）/ "结构化校验失败"，纯动作缺口一律不升格。

    通用性：任何系统只要 agent_result 带 _req_unimplemented 标记即适用，
    不依赖模块名/业务字段，绝不让"需求未实现"漏进引擎侧错误。
    """
    if agent_result and agent_result.get("_req_unimplemented"):
        return "PRODUCT_BUG", "FAIL"
    # ★ D8b（初版，2026-09-23）→ D8b'（同日收窄，见 _has_executed_assertion_verdict）
    if _has_executed_assertion_verdict(agent_result):
        return "PRODUCT_BUG", "FAIL"
    return failure_cat, status


# ★ 判据已抽为共享单点模块 `_assertion_verdict`（2026-09-23）：
#   原实现内联在本文件 → Agent 路径与结构化路径（`_base.py`）各写一份，
#   只改一处时另一处毫无变化（实测 TC-REQ-001-CP01/UX01/SEC01 走**结构化路径**，
#   故 D8/D8b 改在 Agent 路径后分类结果未变，仍 ERROR）。
#   现值保留 `_has_executed_assertion_verdict` 名称作为兼容包装，实体唯一。
def _has_executed_assertion_verdict(agent_result) -> bool:
    """兼容包装：等价于 has_executed_assertion_verdict(agent_result["step_results"])。"""
    return has_executed_assertion_verdict(
        (agent_result or {}).get("step_results", [])
    )


# ★ F3 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：
#   op_cache_v2 replay 场景下"成功后断言"不适用（未真实执行业务动作）——
#   替代原字符串堆叠式后处理补丁，在断言执行层显式过滤。
def _is_replay_skippable_check(check_item) -> bool:
    """判定 check_item 是否为"成功后断言"（replay 场景下应跳过）。

    1) 显式标记：新生成用例（_combo_enhancer F3-4 增加 ``post_success: True``）；
    2) 关键字兜底：历史用例 text_contains_check（UI 轨）expected 含成功文本。
    只跳过"成功文本"类断言（白名单），其他断言（no_error/元素存在等）照常执行，
    避免掩盖真实产品缺陷。
    """
    if not isinstance(check_item, dict):
        return False
    c = check_item.get("check", {})
    if not isinstance(c, dict):
        c = {}
    # 1) 显式标记（新生成用例，见 F3-4）
    if check_item.get("post_success"):
        return True
    # 2) 关键字兜底（历史用例，兼容已落库的 checks）
    if c.get("type") == "text_contains_check" and check_item.get("track") in ("ui", None):
        exp = str(c.get("expected") or check_item.get("expect") or "")
        return any(k in exp for k in (
            "成功", "保存成功", "新增成功", "创建成功", "提交成功",
            "删除成功", "更新成功", "操作成功",
        ))
    return False


def _static_check_expect(check_item) -> str:
    """提取可采信 guided 实时判定的静态 check 期望文本（仅正向文本存在断言）。

    启动资格: 类型为文本存在族（text_contains 等）+ 非 not_contains 否定算子 +
    无选择器限定（限定元素的断言保留真实执行）。不满足返回空串（fail-closed）。
    用途: 同一语义在 guided 层已实时判定时，静态检查层只读采信，不重推。
    """
    if not isinstance(check_item, dict):
        return ""
    _inner = (
        check_item.get("check")
        if isinstance(check_item.get("check"), dict)
        else {}
    )
    _type = str(
        _inner.get("type") or check_item.get("assertion") or ""
    ).strip()
    _base = _type[: -len("_check")] if _type.endswith("_check") else _type
    if _base not in ("text_contains", "contains_text", "text_include"):
        return ""
    _op = str(
        _inner.get("operator") or check_item.get("operator") or ""
    ).strip().lower()
    if _op and _op in ("not_contains", "not", "absent"):
        return ""
    if _inner.get("selector") or check_item.get("selector"):
        return ""
    return str(
        _inner.get("expected") or check_item.get("expect") or ""
    ).strip()


class _BrowserCoreAgentChecksMixin:
    """Agent 探索路径 + 探索后 checks 验证能力。"""

    async def _run_checks_for_agent(
        self,
        engine,
        intent,
        case_id,
        agent_result,
        replay_hit: bool = False,
        is_authoritative: bool = False,
    ):
        """Agent探索后执行checks验证（通用性：跨系统/行业/页面）。

        ★ F3 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：
          replay_hit=True（op_cache_v2 replay 命中，未真实执行业务动作）时，
          在 UI/API 轨执行前过滤"成功后断言"（_is_replay_skippable_check），
          记录 `[SKIP] {desc}: REPLAY_NO_ACTION`，不计入 FAIL；
          返回结果增加 ``replay_skipped`` 标记供调用方决定 SKIP 语义。

        逐条执行intent中的checks，验证Agent探索后的页面状态是否符合预期。
        返回 dict: {all_passed, summary, replay_skipped, ui/API/db 三轨 evidence}。
        """
        _no_page_result = {
            "all_passed": True,
            "summary": "无页面可验证",
            "replay_skipped": False,
            "ui": {"verified": True, "passed": True, "evidence": "无页面"},
            "api": {"verified": False, "passed": False, "evidence": "NOT_AVAILABLE: 无页面"},
            "db": {"verified": False, "passed": False, "evidence": "NOT_AVAILABLE: 无页面", "data": []},
        }
        if not engine or not getattr(engine, "_page", None):
            _log.warning("[AgentChecks] engine无页面，跳过checks验证")
            return _no_page_result

        checks = intent.get("checks", [])
        if not checks:
            _log.info("[AgentChecks] 无checks，跳过验证")
            return {
                "all_passed": True,
                "summary": "无checks定义",
                "replay_skipped": False,
                "ui": {"verified": True, "passed": True, "evidence": "无checks"},
                "api": {"verified": False, "passed": False, "evidence": "NOT_AVAILABLE: 无checks定义"},
                "db": {"verified": False, "passed": False, "evidence": "NOT_AVAILABLE: 无checks定义", "data": []},
            }

        # ★ F3: replay 断言跳过开关（pipeline_config.replay_assertion_skip_enabled）
        _skip_enabled = False
        if replay_hit:
            try:
                from src.core.pipeline_config import _read_config_value as _cfg

                _skip_enabled = bool(_cfg("replay_assertion_skip_enabled", True))
            except Exception:
                _skip_enabled = True

        page = engine._page
        ui_evidence = []
        api_evidence = []
        all_passed = True
        replay_skipped = False

        # ★ 根因修复(2026-09-26) 词表漂移：原实现自带两份硬编码子集
        #   （`_API_DB_TRACK_TYPES` / `_DB_TRACK_TYPES`），与真正的执行注册表
        #   （`_track_protocol` 的 ApiTrackVerifier / DbTrackVerifier）脱钩 ——
        #   实测 `db_table_accessed_check` 已在 DbTrackVerifier 注册（db 轨专用探针），
        #   却不在这两份硬编码集合里 → 被判为 UI 类型 → UI 轨 UNSUPPORTED_CHECK
        #   → "未知检查类型"假 FAIL（实测 TC-REQ-004-P01）。
        #   修复: ① 词表改为投影执行注册表（单一事实源，新增类型只注册一次）；
        #         ② 分派**优先采信 check 自带的 `track` 声明**（Step3 落库的权威声明），
        #            执行端不再用类型猜轨道。
        #   通用性: 跨系统/行业 —— 纯注册表投影 + 元数据采信，零业务硬编码。
        try:
            from src.pipeline.steps.step4.verification._track_protocol import (
                registered_check_types as _registered_check_types,
            )

            _TRACK_TYPES = _registered_check_types()
        except Exception as _rte:  # noqa: BLE001
            _log.warning("[AgentChecks] 执行注册表不可读(降级空集): %r", _rte)
            _TRACK_TYPES = {"ui": frozenset(), "api": frozenset(), "db": frozenset()}
        _API_DB_TRACK_TYPES = set(_TRACK_TYPES.get("api") or ())
        _DB_TRACK_TYPES = set(_TRACK_TYPES.get("db") or ())

        # ★ 断言归一（2026-08-04）：UI 轨 check 统一复用 verification._verify_ui_objective
        #   共享分派（28+ 键 _UI_CHECK_DISPATCH，含 style/toast/position/count/download/
        #   url_check 等），未知 check 类型由该函数 fail-closed（UNSUPPORTED_CHECK → FAIL），
        #   不再落入 `[PASS] 未知check类型, 跳过` 假 PASS 分支。
        ui_check_items = (
            []
        )  # 元素: check_item（原样透传，供 _verify_ui_objective 解析）
        api_check_items = []  # 元素: (check_item, check)
        for i, check_item in enumerate(checks):
            check = check_item.get("check", {}) if isinstance(check_item, dict) else {}
            check_type = check.get("type", "")
            _declared = (
                str(check_item.get("track") or "").strip().lower()
                if isinstance(check_item, dict)
                else ""
            )
            if (
                _declared in ("api", "db")
                or check_type in _API_DB_TRACK_TYPES
                or check_type in _DB_TRACK_TYPES
            ):
                api_check_items.append((check_item, check))
            else:
                ui_check_items.append(check_item)

        # ★ F3: replay 场景过滤"成功后断言"（在 UI/API 轨执行前）
        #   ★  通用性修复：权威需求（is_authoritative=True）不在此列——
        #     需求已定义(is_implicit=0)必须真实验证（PASS/FAIL），replay 绝不得
        #     跳过断言造成 SKIP(REPLAY_NO_ACTION) 掩盖需求满足性。仅非权威/隐含/
        #     AI 虚构需求允许 replay 时跳过"成功后断言"。
        if _skip_enabled and replay_hit and not is_authoritative:
            _kept_ui = []
            for check_item in ui_check_items:
                if _is_replay_skippable_check(check_item):
                    _desc = (
                        check_item.get("description")
                        or check_item.get("expect")
                        or "成功后断言"
                        if isinstance(check_item, dict)
                        else "成功后断言"
                    )
                    ui_evidence.append(
                        f"[SKIP] {_desc}: REPLAY_NO_ACTION（op_cache_v2 replay 未真实执行业务动作，成功后断言不适用）"
                    )
                    replay_skipped = True
                    _log.info(
                        "[AgentChecks] replay 命中，成功后断言跳过(NOT_APPLICABLE): %s",
                        str(_desc)[:80],
                    )
                else:
                    _kept_ui.append(check_item)
            ui_check_items = _kept_ui

        # ── ★ 2026-09-28 静态检查采信 guided 实时判定（上游一次判定，下游只读）──
        #   根因（round 22 实测假 FAIL）: 静态 `checks`(text_contains) 在**流程结束后
        #   的最终页面**上用冷冻字面量重查（成功 flash 已消散/列表已被过滤），而同一
        #   语义已在 guided 实时验证（正确时机、实际值）判定 PASS → 两套断言对同一
        #   期望自相矛盾 → 引擎自造 FAIL。
        #   通用修复: guided 层为上游实时判定（结构化 verify_records），检查层只读
        #   采信——expect 语义相关（local_semantics 共享单一事实源）即直接 PASS 并携带
        #   guided 证据；否定型/限定选择器/无佐证的检查保持真实执行（fail-closed）。
        _guided_verifies: list = []
        for _sr in (agent_result or {}).get("step_results", []) or []:
            if not isinstance(_sr, dict):
                continue
            for _g in _sr.get("_guided_verify_records") or []:
                if (
                    isinstance(_g, dict)
                    and _g.get("passed")
                    and str(_g.get("expect") or "").strip()
                ):
                    _guided_verifies.append(_g)
        # ★ 方法强度门控: 仅**强证据**方法才可被静态检查采信。
        #   根因: 之前不限方法，guided 的「关键词匹配」弱证据（泛词命中）会被静态
        #     semantic_check 采信 → 真失败被盖成 PASS（实测 TC-REQ-002：编辑实际被
        #     SUT 拒绝停在表单，guided 关键词匹配却判"保存成功"）。
        #   强证据 = 以 DOM/URL 可观察状态判定（与 part1 "断言靠 DOM 状态而非文本猜"一致）。
        #   方法名白名单与 guided 验证唯一实现对齐（见 _action.py _guided_verify），
        #   新增强证据方法必须在两处同步登记，避免漂移。
        _STRONG_GUIDED_METHODS = frozenset({
            "成功条检测",
        })
        _guided_verifies = [
            _g for _g in _guided_verifies
            if str(_g.get("method") or "") in _STRONG_GUIDED_METHODS
        ]
        _consumed_ui: list = []
        if _guided_verifies:
            from src.ai.local_semantics import is_semantically_related as _semi_rel

            _kept = []
            for _item in ui_check_items:
                _exp = _static_check_expect(_item)
                if not _exp:
                    _kept.append(_item)
                    continue
                _twin = None
                for _g in _guided_verifies:
                    _gexp = str(_g.get("expect") or "").strip()
                    if (
                        _gexp
                        and str(_g.get("method") or "") != "否定断言"
                        and _semi_rel(_exp, _gexp)
                    ):
                        _twin = _g
                        break
                if _twin is None:
                    _kept.append(_item)
                    continue
                _tw_expect = str(_twin.get("expect") or "").strip()
                _tw_actual = str(_twin.get("actual") or "")[:120]
                _tw_method = str(_twin.get("method") or "?")
                _desc = (
                    _item.get("description")
                    if isinstance(_item, dict)
                    else ""
                ) or "text_contains"
                _consumed_ui.append(
                    {
                        "type": "text_contains",
                        "passed": True,
                        "status": "PASS",
                        "evidence": "[guided-verdict] 实时验证已判定 expect=%r → %r (method=%s)"
                        % (_exp, _tw_actual, _tw_method),
                        "expected": _exp,
                        "description": _desc,
                    }
                )
                _log.info(
                    "[AgentChecks] 静态检查采信 guided 实时判定: "
                    "expect=%r → guided expect=%r (method=%s)",
                    _exp,
                    _tw_expect,
                    _tw_method,
                )
            ui_check_items = _kept

        # ── API 轨：agent_result 调用记录校验（含 api_call_count / db_record_exists 等）──
        for check_item, check in api_check_items:
            description = (
                check_item.get("description", "api_check")
                if isinstance(check_item, dict)
                else "api_check"
            )
            check_type = check.get("type", "")
            selector = check.get("selector", "")
            api_calls = agent_result.get("api_calls", []) if agent_result else []

            if check_type == "api_status":
                matched = False
                for call in api_calls:
                    if isinstance(call, dict) and selector in str(
                        call.get("url", "") or ""
                    ):
                        status = call.get("status", 0)
                        if 200 <= status < 300:
                            matched = True
                            api_evidence.append(
                                f"[PASS] {description}: {selector} -> {status}"
                            )
                            break
                        else:
                            api_evidence.append(
                                f"[FAIL] {description}: {selector} -> {status}"
                            )
                            all_passed = False
                            matched = True
                            break
                if not matched:
                    # ★ : 通用性修复——浏览器上下文无API调用记录时，
                    #   标记为SKIP而非FAIL。目标系统可能为SSR，API调用在服务端不可观测。
                    api_evidence.append(f"[SKIP] {description}: 浏览器上下文无API调用记录(目标系统可能为SSR)")
                    # 不设置 all_passed=False（SKIP 不阻断）

            elif check_type == "api_call_count_check":
                # ★ v5.2: 统计匹配 URL pattern 的 API 调用次数
                expected_count = check.get("expected_count", 1)
                operator = check.get("operator", ">=")
                url_pattern = check.get("url_pattern", "") or selector
                matched_calls = [
                    c for c in api_calls
                    if isinstance(c, dict) and url_pattern in str(c.get("url", "") or "")
                ]
                actual_count = len(matched_calls)
                _passed = False
                if operator == ">=":
                    _passed = actual_count >= int(expected_count)
                elif operator == "<=":
                    _passed = actual_count <= int(expected_count)
                elif operator == ">":
                    _passed = actual_count > int(expected_count)
                elif operator == "<":
                    _passed = actual_count < int(expected_count)
                else:  # eq
                    _passed = actual_count == int(expected_count)

                if _passed:
                    api_evidence.append(
                        f"[PASS] {description}: {actual_count}次调用(期望{operator}{expected_count})"
                    )
                elif actual_count == 0 and int(expected_count) >= 1:
                    # ★ : 通用性修复——浏览器执行上下文未捕获到API调用时，
                    #   标记为SKIP而非FAIL。原因：目标系统可能使用服务端渲染（如Django Admin），
                    #   API调用发生在服务端，浏览器侧无法观测。这不是缺陷，是观测能力限制。
                    #   跨系统/行业通用：任何SSR系统都适用此逻辑。
                    api_evidence.append(
                        f"[SKIP] {description}: 浏览器上下文未捕获API调用(期望{operator}{expected_count}, 实际0次, 目标系统可能为SSR)"
                    )
                else:
                    api_evidence.append(
                        f"[FAIL] {description}: {actual_count}次调用(期望{operator}{expected_count})"
                    )
                    all_passed = False

            elif check_type in _DB_TRACK_TYPES:
                # ★ v5.2: DB 轨 check——浏览器执行上下文无 DB 访问，标记为跳过而非失败
                api_evidence.append(
                    f"[SKIP] {description}: DB轨检查(浏览器执行上下文无DB访问, 跳过)"
                )

            else:
                # 其他 API 轨 check：尝试从 api_calls 验证，无数据则跳过
                if api_calls:
                    api_evidence.append(
                        f"[SKIP] {description}: {check_type}(有{len(api_calls)}条API记录, 跳过详细验证)"
                    )
                else:
                    api_evidence.append(
                        f"[SKIP] {description}: {check_type}(无API调用记录, 跳过)"
                    )

        # ── UI 轨：复用 _verify_ui_objective 共享分派（fail-closed）──
        _ui_checks_list: list = []
        _ui_result: dict = {}
        if ui_check_items:
            try:
                from src.pipeline.steps.step4.verification import _verify_ui_objective

                _ui_result = await _verify_ui_objective(
                    page, {"ui_checks": ui_check_items}
                )
                _ui_checks_list = _ui_result.get("checks", [])
                if not _ui_checks_list:
                    # 理论上不会发生（ui_check_items 非空）；兜底 fail-closed
                    ui_evidence.append("[FAIL] 无有效 UI 断言结果（UNSUPPORTED_CHECK）")
                    all_passed = False
                else:
                    for _r in _ui_checks_list:
                        # ★ 契约修复(2026-09-27): 标签由执行侧唯一权威 render_check_tag 渲染 ——
                        #   skipped/UNVERIFIED 必须显示 [SKIP]/[ERROR]，绝不渲染为 [PASS]
                        #   （结构性假 PASS 根因：原 `"PASS" if passed else "FAIL"` 把
                        #     skipped 的 list_row_count_check「基线不可得」显示为通过）。
                        from src.pipeline.steps.step4.verification._track_protocol import (
                            render_check_tag as _render_tag,
                        )

                        _tag = _render_tag(_r).strip("[]")
                        _type_label = _r.get("type", "?")
                        _ev = _r.get("evidence", "")
                        if _r.get("reason") == "UNSUPPORTED_CHECK":
                            _type_label = f"{_type_label}(未知check类型)"
                        ui_evidence.append(f"[{_tag}] {_type_label}: {_ev}")
                    if not _ui_result.get("passed"):
                        all_passed = False
            except Exception as _ue:
                ui_evidence.append(f"[FAIL] 共享断言分派异常({str(_ue)[:60]})")
                all_passed = False

        # ★ 2026-09-28: guided 采信记录并入 UI 判定结果与展示证据（判据来源=
        #   实时验证，非最终页重查；render_check_tag 按 status=PASS 渲染 [PASS]）
        if _consumed_ui:
            ui_evidence.extend(
                "[PASS] %s: %s" % (_r["type"], _r["evidence"])
                for _r in _consumed_ui
            )
            _ui_checks_list = list(_consumed_ui) + _ui_checks_list

        summary_parts = []
        if ui_evidence:
            summary_parts.append("UI: " + "; ".join(ui_evidence[:5]))
        if api_evidence:
            summary_parts.append("API: " + "; ".join(api_evidence[:5]))
        summary = " | ".join(summary_parts) if summary_parts else "无验证结果"

        # ★ 契约修复(2026-09-27): 轨通过判定改用执行侧三值聚合 —— skipped/UNVERIFIED
        #   不再计为通过（原 `all(startswith("[PASS]"))` 会把 [SKIP] 判成不通过、
        #   又把 skipped 的 check 当 PASS，两个方向都失真）。字符串前缀仅作展示。
        from src.pipeline.steps.step4.verification._track_protocol import (
            aggregate_check_results as _agg_checks,
        )

        ui_all_pass = (
            bool(_agg_checks(_ui_checks_list)["passed"]) if _ui_checks_list else True
        )
        # API 轨：无 [FAIL] 即不阻断（[SKIP] 属无结论，非失败）
        api_all_pass = (
            all(not e.startswith("[FAIL]") for e in api_evidence)
            if api_evidence
            else True
        )

        # ── ★ 意图驱动路径DB验证增强(2026-08-09, 用户问题 #3/#5): ──
        #   根因: 意图驱动路径的 DB 轨始终标记为 NOT_AVAILABLE，即使系统配置了 DB 访问。
        #   修复: 检查 DB 配置，若可用则通过 verify_db 执行实际 DB 校验。
        #   通用性: 跨系统/行业 — 配置了 DB 访问的系统自动启用，未配置的不受影响。
        # ★ 根因修复(2026-09-26)：原实现另有两条缺陷——
        #   ① 过滤只用硬编码 `_DB_TRACK_TYPES`（3 个旧类型）→ `db_table_accessed_check`
        #      等已注册类型永远取不到（词表漂移，与上面 UI/API 分派同源）；
        #   ② 直接用 `verify_db`，其"DB 连接失败 → skipped/success=None"的诚实语义
        #      在这里被 `"PASS" if success else "FAIL"` 反转成 FAIL（环境问题记成
        #      产品缺陷），且与 DbTrackVerifier 的 UNVERIFIED/skipped 协议不一致。
        #   修复: 一律委托 DB 轨权威适配层 `_verify_db_async`（按类型分派到注册器 +
        #      skipped/UNVERIFIED 诚实语义），并按 check 自带 `track` 声明选型。
        #   通用性: 跨系统/行业 —— 纯注册表投影，零业务硬编码。
        _db_evidence = "NOT_AVAILABLE: 未配置DB直接访问"
        _db_verified = False
        _db_passed = False
        _db_data = []
        with guard("_run_checks_for_agent: _db_checks = [ c for c in checks if ", log=_log):
            _db_checks = [
                c
                for c in checks
                if isinstance(c, dict)
                and (
                    str(c.get("track") or "").strip().lower() == "db"
                    or (c.get("check") or {}).get("type", "") in _DB_TRACK_TYPES
                )
            ]
            if _db_checks:
                from src.pipeline.steps.step4.verification._db_ui import (
                    _verify_db_async as _verify_db_track,
                )

                _r_db = await _verify_db_track(_db_checks, page)
                _db_evidence = str(_r_db.get("evidence") or "") or _db_evidence
                _db_data = _r_db.get("data") or []
                _db_verified = not bool(_r_db.get("skipped"))
                _db_passed = bool(_r_db.get("passed"))
                if _db_verified and not _db_passed:
                    all_passed = False

        # DB 轨证据并入摘要（原先只拼 UI/API → DB 轨失败时 summary 无从归因，
        # 报告 expected/actual 证据链不完整）
        if _db_verified and _db_evidence:
            summary_parts.append("DB: " + _db_evidence[:200])
            summary = " | ".join(summary_parts)
        return {
            "all_passed": all_passed,
            "summary": summary[:500],
            "replay_skipped": replay_skipped,
            # ★ 2026-09-23 反假PASS守卫: 是否存在实质断言证据。
            #   平凡检查（整页兜底 element_exists / no_error）通过 ≠ 期望达成；
            #   语义判定/字段错误/状态断言/**具体选择器的 element_exists** 才算实质证据。
            #   ★ F7b(2026-09-24): 平凡性判据下沉到 _is_trivial_check（按选择器区分），
            #     不再按类型一刀切 —— 否则具体选择器的真断言会被误降级 SKIP。
            "has_substantive_check": any(
                _r.get("passed") and not _is_trivial_check(_r)
                for _r in _ui_checks_list
            ),
            # ★ 契约修复(2026-09-27): 判据委托执行侧三值聚合 —— 「无任何有效判定」
            #   （全 skipped/UNVERIFIED/UNSUPPORTED/ERROR）即为无结论 → SKIP；
            #   只有存在可归因产品缺陷的失败（has_product_defect_verdict）才归 FAIL。
            #   根因: 原判据只认 status=="UNVERIFIED"，把 skipped/UNKNOWN 的用例
            #   漏判成 FAIL（假 Bug）——如全部 list_row_count_check 因基线不可得而 skip。
            "unverified_only": bool(
                (not all_passed)
                and not _ui_result.get("has_product_defect_verdict", False)
                and (
                    _ui_result.get("no_verdict_only", False)
                    or not _ui_result.get("has_positive_verdict", True)
                )
                and all(
                    e.startswith("[PASS]") or e.startswith("[SKIP]")
                    for e in api_evidence
                )
                and (not _db_verified or _db_passed)
            ),
            "ui": {
                "verified": bool(ui_evidence),  # ★ v11: 有实际UI证据才算verified
                "passed": ui_all_pass,
                "evidence": "; ".join(ui_evidence[:10]) if ui_evidence else "无UI校验",
                # 透传三值判定元数据与逐条结果：
                # 若只保留 passed/evidence，会丢掉 has_negative_verdict/no_verdict_only，
                # 迫使下游从渲染文本重推判定，引擎自述"不适用"的 ⊘ 会被误判为产品缺陷。
                # 语义唯一：上游判定、下游只读。
                "checks": _ui_result.get("checks", []),
                "has_positive_verdict": _ui_result.get("has_positive_verdict", False),
                "has_negative_verdict": _ui_result.get("has_negative_verdict", False),
                "has_product_defect_verdict": _ui_result.get(
                    "has_product_defect_verdict", False
                ),
                "no_verdict_only": _ui_result.get("no_verdict_only", False),
            },
            "api": {
                "verified": bool(api_evidence),  # ★ v11: 有实际API证据才算verified
                "passed": api_all_pass,
                "evidence": (
                    "; ".join(api_evidence[:10]) if api_evidence else "无API校验"
                ),
                "calls": agent_result.get("api_calls", []) if agent_result else [],
            },
            "db": {
                "verified": _db_verified,
                "passed": _db_passed,
                "evidence": _db_evidence,
                "data": _db_data,
            },
        }

    async def _try_agent_explorer_path(
        self,
        engine,
        intent,
        case_meta,
        case_id,
        step_results,
        all_passed,
        intent_t0,
        _session_login_failed,
        intent_desc,
    ):
        """AgentExplorer 实时探索路径（P0-refactor, §16.2）。

        所有含 _explore_task 的用例（Step3 统一生成）走此路径；返回 agent_result
        （已含 record_execution 落库 / checks 验证 / 置信度闸门）或 None
        （无 _explore_task 的遗留用例走标准 step loop，向后兼容）。
        拆出原因: §4.3 单文件≤800行（2026-08-06），逻辑零变更。
        """
        from ._agent_executor import handle_agent_intent as _handle_agent

        # ★ F3 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：
        #   原"L299-319 字符串堆叠后处理 FAIL→SKIP 补丁"已删除，
        #   改为 _run_checks_for_agent 执行层对 replay 命中用例显式过滤"成功后断言"
        #   （_is_replay_skippable_check）→ SKIP(REPLAY_NO_ACTION)。
        _agent_result = await _handle_agent(
            engine,
            intent,
            case_meta,
            case_id,
            step_results,
            all_passed,
            intent_t0,
            _session_login_failed,
        )
        if _agent_result is None:
            return None
        # ★ P0-fix(2026-07-28): AgentExplorer 路径补 record_execution 落库
        #   根因(§10.9 4层追问):
        #     1. 现象: 21条exploratory用例执行5条，executions表只有1条PRE_GATE，0条业务记录
        #     2. 直接原因: record_execution 未被调用
        #     3. 中间原因: handle_agent_intent 返回后 _execute_one_intent 直接 return，
        #        跳过了标准 step loop 末尾的 record_execution
        #     4. 根因: 重构AgentExplorer路径时只关注"让Agent能执行"，忽略了
        #        "执行结果必须落库"的数据完整性要求
        #   通用性: 跨系统/行业/页面 — 所有AgentExplorer路径都受影响
        #   覆盖性: 所有走 handle_agent_intent 的用例都受益
        #   全局观: 修复后 Step5 报告有数据、SelfEval有数据、校准环有回流
        #   职责边界: Step4 内部数据流修复，不越界
        #   自我反驳:
        #     1) 在_exploratory.py中调用record_execution? → 增加耦合，违反职责边界
        #     2) 不让AgentExplorer提前返回，走标准流程? → 破坏AgentExplorer独立性
        #     3) 在serial.py中统一调用? → 太晚，serial.py只拿到聚合结果
        with guard("_try_agent_explorer_path: from src.core.config_loader import g", log=_log, level=logging.WARNING):
            from src.core.config_loader import get_current_user
            from src.users.audit import record_execution
            from src.backend.state import _thread_run_id

            _uid = get_current_user() or "anonymous"
            _run_id = getattr(_thread_run_id, "value", "") or ""
            _project = getattr(config, "project_name", "") or ""
            _passed = _agent_result.get("passed", False)
            _skipped = _agent_result.get("_skipped", False)
            _status = "SKIP" if _skipped else ("PASS" if _passed else "FAIL")
            _failure = _agent_result.get("error", "") if not _skipped else _agent_result.get("skip_reason", "TARGET_SYSTEM_LIMITATION")
            _failure_cat = ""
            # ★ 根因修复(2026-10-28) 无断言不得记 PASS（Agent 路径）：
            #   实测 checks 为空的 4 条用例以引导层通用探针（page_loaded/任意错误条）
            #   判 PASS（conf 0.88~0.93），探针不承载需求义务 → 结构性假绿。
            #   （本函数第 897 行 `and intent.get("checks")` 使 checks 为空时探索后
            #     断言验证被整体跳过，状态直接沿用 _passed=True → PASS。）
            #   判据单点收口 `_assertion_verdict.declares_executable_assertion`
            #   （结构化路径 `_base._execute_one_intent` 同源调用，禁止两处各自拼装）。
            if _passed and not _skipped and not declares_executable_assertion(intent):
                _skipped = True
                _status = "SKIP"
                _failure_cat = "ENGINE_COVERAGE_GAP"
                _failure = (
                    "【SKIP】用例未声明任何可执行断言（checks 为空）→ 引导层通用探针"
                    "（页面已加载/任意错误条）不能作为需求义务的证据：不记 PASS（防假绿），"
                    "也不记 FAIL（防假 Bug）；需补齐断言或转人工补测"
                )
                _agent_result["_skipped"] = True
                _agent_result["skip_reason"] = _failure
                _log.info(
                    "[AgentRecord] 无断言不计PASS→SKIP(ENGINE_COVERAGE_GAP): %s",
                    case_id,
                )
            # ★ 根因修复（2026-09-23）D9: SKIP 行缺结构化归因分类
            #   根因: 分类逻辑整体嵌在 `if not _passed:` 之内，而「探索判定为
            #     pass 但被跳过」的 SKIP（如非权威需求不可达 / replay / 断言不可判定）
            #     走不到该分支 → failure_category 恒为 ""，报告与 KPI 无法按跳过原因
            #     分桶（实测审计全部落 "(无分类)"），跳过原因不可统计、不可审计。
            #   修复: 在唯一写入点按引擎既有跳过语义词表补全分类，通用（不依赖
            #     业务/模块名），不改动任何判定结果。
            if _status == "SKIP" and not _failure_cat:
                _skip_blob = " ".join(
                    [
                        str(_failure or ""),
                        str(_agent_result.get("skip_reason", "") or ""),
                    ]
                )
                _skip_low = _skip_blob.lower()
                if "replay" in _skip_low or "REPLAY_NO_ACTION" in _skip_blob:
                    _failure_cat = "REPLAY_NO_ACTION"
                elif (
                    "ASSERTION_UNVERIFIED" in _skip_blob
                    or "语义断言" in _skip_blob
                    or "无法判定" in _skip_blob
                ):
                    _failure_cat = "ASSERTION_UNVERIFIED"
                elif "ENGINE_COVERAGE_GAP" in _skip_blob:
                    _failure_cat = "ENGINE_COVERAGE_GAP"
                else:
                    _failure_cat = "TARGET_SYSTEM_LIMITATION"
                _log.info(
                    "[AgentRecord] SKIP 归因补全: category=%s case=%s",
                    _failure_cat,
                    case_id,
                )
            _chain = None  # ★ F1: 失败链路对象（供 AI 根因增补复用，PASS 路径保持 None）
            if not _passed:
                with guard("_try_agent_explorer_path: from src.pipeline.steps.step4.failur", log=_log):
                    from src.pipeline.steps.step4.failure_classifier import (
                        FailureChain,
                        classify_failure,
                        build_failure_reason,
                    )

                    _chain = FailureChain()
                    _sr = _agent_result.get("step_results", [])
                    _has_to = False
                    for _r in _sr:
                        _err = (_r.get("error") or "").lower()
                        if "timeout" in _err or "timed out" in _err:
                            _has_to = True
                        _chain.add_step(
                            strategy=_r.get("action")
                            or _r.get("description")
                            or "agent",
                            success=_r.get("passed", False),
                            detail=_r.get("error", ""),
                            # ★ P0-fix(2026-07-28): Agent穷举搜索后传dom_has_element
                            dom_has_element=_r.get("_dom_has_element"),
                        )
                    _failure_cat, _status = classify_failure(
                        _chain,
                        timeout_error=_has_to,
                        # ★ BUG-1(2026-08-05): 并入导航阶段网络错误信号（Agent 探索
                        #   路径同样受益：目标不可达 → ENVIRONMENT_ERROR）
                        network_error=bool(
                            getattr(engine, "_last_network_error", False)
                        ),
                    )
                    # ★  根因修复: 权威需求未实现（_req_unimplemented=True）
                    #   时，失败链路仅 1 步（assert 断言步骤）→ classify_failure 按
                    #   "策略数<2 且 DOM 状态未知" 误判为 HARNESS_ERROR(ERROR)。
                    #   通用性: 需求已定义(is_implicit=0)但系统未实现 = 产品缺陷缺口，
                    #   与失败链路形态无关 —— 任何系统只要走到此标记就应判 PRODUCT_BUG。
                    #   修复: 语义覆盖（见 _finalize_agent_failure），绝不让"需求未实现"
                    #   漏进引擎侧错误。
                    _failure_cat, _status = _finalize_agent_failure(
                        _failure_cat, _status, _agent_result
                    )
                    if not _failure:
                        _failure = build_failure_reason(_chain)
            # ★ P0-fix: Agent探索成功后执行checks验证（假PASS根因修复）
            #   根因(§10.9 5层why):
            #     1. 现象: id=53 PASS, evidence=fallback_no_verify → 假PASS
            #     2. 直接原因: extract_evidence_from_step_results找不到_verify_result
            #     3. 中间原因: AgentExplorer路径不执行intent中的checks
            #     4. 设计原因: handle_agent_intent只关注探索成功/失败，不验证业务预期
            #     5. 根因: AgentExplorer路径缺少"探索后验证"环节
            #   通用性: 跨系统/行业/页面 — 所有exploratory用例都受益
            #   覆盖性: 所有走handle_agent_intent的用例都执行checks验证
            if _passed and not _skipped and intent.get("checks"):
                with guard("_try_agent_explorer_path: from ._base_classify import ( _is_au", log=_log, level=logging.WARNING):
                    # ★  通用性修复：权威需求单一事实源门禁——
                    #   replay 命中也不得跳过"成功后断言"→ SKIP(REPLAY_NO_ACTION)。
                    #   （_agent_executor 已对权威需求强制 skip_replay 实时执行，
                    #    此处为纵深防御：任何残留 replay 信号 + 权威需求都不得 SKIP。）
                    from ._base_classify import (
                        _is_authoritative_requirement as _is_auth,
                    )

                    _is_authoritative = _is_auth(intent, case_meta)
                    # ★ F3 全局修复 [未复核-需 GLM/DeepSeek 把关]：replay 命中标记
                    #   （Agent 路径 op_cache_v2 命中 → intent.op_cache_hits>0，
                    #    由 _agent_executor L358-361 写回）→ 执行层过滤成功后断言。
                    _replay_hit = bool(intent.get("op_cache_hits", 0) > 0)
                    _verify_result = await self._run_checks_for_agent(
                        engine,
                        intent,
                        case_id,
                        _agent_result,
                        replay_hit=_replay_hit,
                        is_authoritative=_is_authoritative,
                    )
                    if _verify_result:
                        _agent_result["step_results"].append(
                            {
                                "step_idx": -1,
                                "description": "Agent探索后checks验证",
                                "action": "verify_checks",
                                "passed": _verify_result.get("all_passed", True),
                                "error": _verify_result.get("summary", ""),
                                "elapsed": 0.0,
                                "_hard_verified": True,
                                "_verify_result": _verify_result,
                            }
                        )
                        if not _verify_result.get("all_passed", True):
                            # ★  根因修复: 唯一失败项全部为 UNVERIFIED
                            #   （机械文本未命中 + AI 语义判定不可用）→ 引擎断言能力
                            #   缺口，不是产品缺陷证据 ⇒ SKIP(ASSERTION_UNVERIFIED)，
                            #   既不判假 FAIL 也不假 PASS。
                            #   根因: 语义期望被生成端翻译成字面 needle 做 contains，
                            #   页面用别的措辞表达同一语义时必然 0 匹配（17 条实测假 FAIL）。
                            if _verify_result.get("unverified_only"):
                                _skipped = True
                                _passed = False
                                _status = "SKIP"
                                _failure_cat = "ASSERTION_UNVERIFIED"
                                _failure = (
                                    "语义断言无法判定（机械文本未命中且 AI 判定不可用）: "
                                    + str(_verify_result.get("summary", ""))[:300]
                                )
                                _agent_result["_skipped"] = True
                                _agent_result["skip_reason"] = _failure
                                _log.info(
                                    "[AgentRecord] 断言不可判定→SKIP(ASSERTION_UNVERIFIED): %s",
                                    case_id,
                                )
                            else:
                                _passed = False
                                # ★  根因修复：探索成功后业务 checks 验证失败，
                                #   此前 _failure_cat 保持 ""（仅初始 Agent 失败才走
                                #   classify_failure）→ FAIL 记录无 failure_category。
                                #   通用性: 任何"探索成功但业务断言失败"的用例都会漏分类。
                                #   修复: 按 checks 结果统一归类，绝不留空。
                                _failure_cat, _status = _classify_checks_failure(
                                    _verify_result, engine
                                )
                                _failure = _failure or _verify_result.get(
                                    "summary", "checks未通过"
                                )
                                # ★ D18：分类器判定为引擎不可评估（ENGINE_COVERAGE_GAP）
                                #   → 与既有 SKIP 分支保持同一语义：同步 _skipped/skip_reason，
                                #   使下游聚合与 KPI 按"跳过"而非"失败"消费（防假 Bug 传导）。
                                if _status == "SKIP":
                                    _skipped = True
                                    _failure = (
                                        "【SKIP】失败项全部为引擎不可评估的检查"
                                        "（未知检查类型/执行器异常/断言不适用），"
                                        "无产品缺陷证据 → 不记 FAIL（防假 Bug）: "
                                        + str(_verify_result.get("summary", ""))[:300]
                                    )
                                    _agent_result["_skipped"] = True
                                    _agent_result["skip_reason"] = _failure
                                    _log.info(
                                        "[AgentRecord] 引擎缺口转 SKIP"
                                        "(ENGINE_COVERAGE_GAP): %s",
                                        case_id,
                                    )
                            _log.warning(
                                "[AgentRecord] checks验证失败: %s (category=%s)",
                                _verify_result.get("summary", "")[:120],
                                _failure_cat,
                            )
                        elif _verify_result.get("replay_skipped") and not _is_authoritative:
                            # ★ F3 全局修复 [未复核-需 GLM/DeepSeek 把关]：
                            #   replay 命中且成功后断言被跳过、其余断言全部通过 →
                            #   业务动作未真实执行，SKIP(REPLAY_NO_ACTION) 而非 PASS/FAIL。
                            #   ★  通用性修复：权威需求绝不允许 SKIP ——
                            #   需求已定义必须真实验证，replay 跳过断言不能掩盖需求满足性。
                            _status = "SKIP"
                            _failure_cat = "REPLAY_NO_ACTION"
                            _failure = (
                                "op_cache_v2 replay 命中，成功后断言未执行(NOT_APPLICABLE): REPLAY_NO_ACTION"
                            )
                            # 同步 agent_result 的 skip 语义（与 _agent_executor
                            # TARGET_SYSTEM_LIMITATION SKIP 路径一致，供下游消费）
                            _agent_result["_skipped"] = True
                            _agent_result["skip_reason"] = _failure
                            _log.info(
                                "[AgentRecord] replay 命中→SKIP(REPLAY_NO_ACTION): %s",
                                case_id,
                            )
                        elif _verify_result.get(
                            "all_passed", True
                        ) and not _verify_result.get("has_substantive_check", True):
                            # ★ 2026-09-23 反假PASS守卫（fail-closed，用户拍板）[未复核-需 GLM/DeepSeek 把关]:
                            #   checks 全部通过但全部是平凡检查（element_exists/no_error），
                            #   且 agent 证据中无强验证（语义断言/文本匹配/否定断言），
                            #   仅 replay 命中或泛关键词匹配 → PASS 证据不足。
                            #   诚实降级 SKIP(ASSERTION_UNVERIFIED)，绝不冒充 PASS。
                            import json as _json_wp

                            try:
                                _sr_blob = _json_wp.dumps(
                                    [
                                        {
                                            k: _sr.get(k, "")
                                            for k in ("description", "error", "action")
                                        }
                                        for _sr in (
                                            _agent_result.get("step_results") or []
                                        )
                                        if isinstance(_sr, dict)
                                    ],
                                    ensure_ascii=False,
                                )
                            except Exception:
                                _sr_blob = str(
                                    _agent_result.get("step_results", "")
                                )[:2000]
                            _has_strong_verify = any(
                                m in _sr_blob
                                for m in (
                                    "验证通过[语义断言]",
                                    "验证通过[文本匹配]",
                                    "验证通过[否定断言]",
                                )
                            )
                            _keyword_only = (
                                "验证通过[关键词匹配]" in _sr_blob
                                and not _has_strong_verify
                            )
                            _replay_only = (
                                "op_cache_v2 replay命中" in _sr_blob
                                and "验证通过[" not in _sr_blob
                            )
                            _no_verify_at_all = "验证通过[" not in _sr_blob
                            if (
                                _keyword_only
                                or _replay_only
                                or _no_verify_at_all
                            ):
                                _skipped = True
                                _passed = False
                                _status = "SKIP"
                                _failure_cat = "ASSERTION_UNVERIFIED"
                                _weak_why = (
                                    "仅泛关键词匹配"
                                    if _keyword_only
                                    else ("仅replay缓存命中" if _replay_only else "无任何验证点执行")
                                )
                                _failure = (
                                    "PASS证据不足（防假PASS降级）: 断言仅由平凡检查"
                                    "(element_exists/no_error)构成且%s，"
                                    "无法证明期望达成 → 诚实降级 SKIP: "
                                    % _weak_why
                                    + str(_verify_result.get("summary", ""))[:200]
                                )
                                _agent_result["_skipped"] = True
                                _agent_result["skip_reason"] = _failure
                                _log.info(
                                    "[AgentRecord] 弱证据PASS→SKIP(ASSERTION_UNVERIFIED,%s): %s",
                                    _weak_why,
                                    case_id,
                                )
            # evidence 提取：从 agent step_results 提取
            _expected = ""
            _actual = ""
            _details_json = ""
            with guard("_try_agent_explorer_path: from src.pipeline.steps.step4.browse", log=_log, level=logging.WARNING):
                from src.pipeline.steps.step4.browser_core.single_mixins._evidence_extractor import (
                    declared_expectation,
                    extract_evidence_from_step_results,
                )

                _expected, _actual, _check_details = extract_evidence_from_step_results(
                    _agent_result.get("step_results", []),
                    declared_expected=declared_expectation(intent, case_meta),
                )
                import json as _json3

                _details_json = (
                    _json3.dumps(_check_details, ensure_ascii=False)
                    if _check_details
                    else ""
                )
            # ★ F1 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：
            #   失败分类/根因分析接入 AI —— enrich_root_cause_ai 规则判定后增补，
            #   合并进 details JSON（追加 ai_root_cause/ai_severity/ai_suggestion/
            #   ai_confidence/ai_model/ai_source），**绝不覆盖** status/
            #   failure_category/failure_reason。任何失败 fail-open 返回 {} 等同现状。
            if _chain is not None and _status in ("FAIL", "ERROR"):
                with guard("_try_agent_explorer_path: from src.pipeline.steps.step4.failur", log=_log):
                    from src.pipeline.steps.step4.failure_classifier import (
                        enrich_root_cause_ai as _enrich_ai,
                    )

                    _ai_enrich = _enrich_ai(
                        _chain,
                        expected=_expected,
                        actual=_actual,
                        failure_reason=_failure or "",
                        category=_failure_cat or "",
                        status=_status or "",
                    )
                    if _ai_enrich:
                        import json as _json_ai

                        _det = _json_ai.loads(_details_json) if _details_json else {}
                        if isinstance(_det, list):
                            _det = {"_items": _det}
                        if not isinstance(_det, dict):
                            _det = {}
                        _det.update(_ai_enrich)
                        _details_json = _json_ai.dumps(_det, ensure_ascii=False)
                        _log.info(
                            "[AgentRecord] AI 根因增补合并 details: case_id=%s keys=%s",
                            case_id,
                            ",".join(
                                sorted(k for k in _ai_enrich if k.startswith("ai_"))
                            ),
                        )
            _execution_id = record_execution(
                user_id=_uid,
                project=_project,
                case_id=case_id,
                status=_status,
                case_asset_file=case_meta.get("case_asset_file", ""),
                case_row=_safe_int(case_meta.get("case_row", 0)),
                screenshot="",
                failure_reason=_failure,
                failure_category=_failure_cat,
                run_id=_run_id,
                check_type="browser_exec",
                expected=_expected,
                actual=_actual,
                details=_details_json,
            )
            _log.info(
                "[AgentRecord] execution落库: case_id=%s status=%s execution_id=%s",
                case_id,
                _status,
                _execution_id,
            )
            # ★ P0-fix(2026-07-28): AgentExplorer 路径补置信度闸门
            #   根因(§10.9 4层追问):
            #     1. 现象: 8条用例 conf=0.0 但 status=PASS（假PASS）
            #     2. 直接原因: ConfidenceCalculator.calculate() 从未被调用
            #     3. 中间原因: apply_confidence_gate 在标准路径(731行)调用，
            #        但AgentExplorer路径在332行提前return，绕过了闸门
            #     4. 根因: P0-refactor添加AgentExplorer路径时只补了record_execution
            #        落库，未补apply_confidence_gate置信度闸门
            #   通用性: 跨系统/行业/页面 — 所有AgentExplorer路径都受影响
            #   覆盖性: 所有走handle_agent_intent的用例都受益
            #   全局观: 修复后Step5报告有准确置信度，假PASS检测生效
            #   职责边界: Step4内部数据流修复，不越界
            #   自我反驳:
            #     1) 只在AgentExplorer路径加闸门? → 标准路径已有闸门(731行)，此处补漏
            #     2) 应该改ConfidenceCalculator? → 计算器本身没问题，是调用链路缺失
            #     3) 有更根本方案? → 统一两条路径的record_execution后处理，但当前最小改动最优
            # ★ v12(2026-08-09): 移除 _status == "PASS" 限制——所有用例都计算置信度
            #   根因: 原代码 `if _status == "PASS":` 导致 FAIL 用例从未计算置信度，
            #   confidence 恒为 0.0，confidence_factors 恒为 NULL。_confidence_gate.py
            #   内部已通过 status=="PASS" 守卫闸门逻辑，FAIL 用例只计算不改变状态。
            #   通用性: 跨系统/行业/页面 — 所有状态的置信度都应计算，不依赖状态值。
            # ★ SecurityScanner 集成: Agent 路径安全扫描
            #  通用性: 与标准路径一致，Agent 路径也需要安全扫描
            _agent_security_findings: list[dict] = []
            with guard("_try_agent_explorer_path: from src.extensions.security_scanner", log=_log):
                from src.extensions.security_scanner import SecurityScanner

                _agent_scanner = SecurityScanner()
                if engine._page is not None:
                    _agent_page_content = await engine._page.content()
                    _agent_page_url = engine._page.url or ""
                    _agent_scanner.scan_response(
                        body=_agent_page_content,
                        content_type="text/html",
                        url=_agent_page_url,
                        status_code=200,
                        headers=None,
                    )
                _agent_security_findings = _agent_scanner.to_dict_list()
                if _agent_security_findings:
                    _log.warning(
                        "[AgentRecord] SecurityScanner: case_id=%s 发现 %d 个安全问题",
                        case_id,
                        len(_agent_security_findings),
                    )

            with guard("_try_agent_explorer_path: from src.pipeline.steps.step4.browse", log=_log):
                from src.pipeline.steps.step4.browser_core.single_mixins._confidence_gate import (
                    apply_confidence_gate,
                )

                _intent_schema, _intent_payload = self._extract_intent_schema(
                    intent
                )
                _agent_passed_val = apply_confidence_gate(
                    _status,
                    _passed,
                    _execution_id,
                    case_id,
                    case_meta,
                    _intent_payload,
                    intent,
                    intent_desc,
                    [],  # AgentExplorer无传统steps，step_results在agent路径已记录
                    _agent_result.get("step_results", []),
                    expected=_expected,
                    actual=_actual,
                    details=_details_json,
                    security_findings=_agent_security_findings,
                    # ★ P0-3 增量 [未复核-需 GLM/DeepSeek 把关]：深断言信号标记
                    adi_signal=bool(intent.get("adi_signal", False)),
                )
                if _status == "PASS" and not _agent_passed_val:
                    _agent_result["passed"] = False
                    _agent_result["error"] = (
                        _agent_result.get("error", "") or "置信度不足(<70%)"
                    )
                    _log.warning(
                        "[AgentRecord] 置信度闸门拦截: case_id=%s → FAIL", case_id
                    )
            # ★ 每个用例终态截图落库（agent-explorer/replay 路径同样覆盖，Task② 补漏）：
            #   _execute_one_intent 在 agent 路径拿到结果会提前 return（_base.py:340-341），
            #   走不到末尾的标准截图 hook，故在 agent 路径自己的落库/置信度闸门之后补拍。
            with guard("_try_agent_explorer_path: await self._save_case_screenshot(cas", log=_log, level=logging.WARNING):
                await self._save_case_screenshot(case_id, _status, _project, _run_id)
        # ★ SecurityScanner: 将安全发现附加到 agent 结果
        if isinstance(_agent_result, dict):
            _agent_result["security_findings"] = _agent_security_findings
        return _agent_result
