# -*- coding: utf-8 -*-
"""
置信度计算器 —— 计算核心 mixin
=====================================
权重管理 + 单条测试用例置信度计算。从 ``confidence_calculator.py`` 拆分而来，逻辑保持不变。

置信度公式：
  confidence = (
    assertion_rate * 0.35 +      # 断言通过率（最客观）— 从 evidence_items / checks[] 验证结果计算
    case_completeness * 0.25 +  # 用例完整度（核心字段填充率，替代旧 structured_rate）
    hard_verify_rate * 0.20 +    # 硬验证通过率（防幻觉）
    semantic_align * 0.15 +      # 语义对齐度（需求-用例匹配）
    cache_hit_rate * 0.05        # 经验复用命中率（op_cache + capability_cache）
  )
"""

import logging
import json
import threading
from typing import Any
from src.core.pipeline_config import pipeline_config

_log = logging.getLogger("confidence")


class ConfidenceCalculatorCalculateMixin:
    """计算核心 mixin：权重管理 + 单条置信度计算。★ P2-03: 权重改为实例变量，避免多线程竞争。"""

    # 各因子权重（★ KEEP-08: 从 PipelineConfig 读取，可通过环境变量覆盖）
    DEFAULT_WEIGHTS = {
        "assertion_rate": pipeline_config.confidence_weight_assertion,
        "case_completeness": pipeline_config.confidence_weight_structured,
        "hard_verify_rate": pipeline_config.confidence_weight_hard_verify,
        "first_try_rate": pipeline_config.confidence_weight_first_try,
        "semantic_align": pipeline_config.confidence_weight_semantic,
        "cache_hit_rate": pipeline_config.confidence_weight_cache,
    }

    def __init__(self):
        # ★ P2-03: 实例变量替代类变量，每个实例独立持有权重副本
        self.weights = dict(self.DEFAULT_WEIGHTS)
        self._weights_lock = threading.RLock()
        self._feedback_count = 0
        self._last_calibration_count = 0
        # ★ F-04④ (2026-08-04): 权重和校验 — 六项权重之和必须 == 1.0（±0.001），
        #   否则拒绝启动。根因(3层): ①权重从环境变量 CONFIDENCE_WEIGHT_* 读取且
        #   无总和校验，同一用例在不同环境可得不同 confidence（Q11 阈值语义不稳）；
        #   ②0.70 阈值要成立，被比较的数本身必须稳定；③报告中须随 confidence
        #   输出权重版本号使历史结论可复算。
        _weight_sum = sum(self.weights.values())
        if abs(_weight_sum - 1.0) > 0.001:
            raise RuntimeError(
                "[ConfidenceCalc] 权重总和校验失败: %.4f != 1.0（±0.001）。"
                "请检查 CONFIDENCE_WEIGHT_* 环境变量，六项权重之和必须为 1.0。"
                "weights=%s" % (_weight_sum, self.weights)
            )

    def get_weights(self) -> dict[str, float]:
        with self._weights_lock:
            return dict(self.weights)

    def set_weights(self, weights: dict[str, float]) -> None:
        with self._weights_lock:
            self.weights = dict(weights)

    # 重试轮次越多 → 置信度越高惩罚越大（指数衰减）
    @staticmethod
    def _rounds_penalty(rounds_needed):
        """0轮=1.0, 1轮=1.0, 3轮=0.70, 5轮=0.40, 8轮=0.0"""
        if rounds_needed is None or rounds_needed <= 1:
            return 1.0
        if rounds_needed >= 8:
            return 0.0
        # rounds=2 → 0.85, rounds=3 → 0.70, rounds=5 → 0.40
        return max(0.0, 1.0 - (rounds_needed - 1) * 0.15)

    def calculate(self, intent_result: dict[str, Any]) -> dict[str, Any]:
        return self._calculate(intent_result)

    def _calculate(self, intent_result: dict[str, Any]) -> dict[str, Any]:
        """计算单条测试用例的置信度。

        Args:
            intent_result: 用例执行结果，包含：
                - steps: 步骤列表（每个含 _structured, assertions, _hard_verified）
                - passed: 是否通过
                - step_results: 每步执行结果
                - cache_hits: 缓存命中次数
                - cache_misses: 缓存未命中次数

        Returns:
            {
                "confidence": 0.0-1.0,
                "level": "high" | "medium" | "low",
                "factors": {各因子详情},
                "suggestion": "自动通过" | "建议复核" | "需人工确认"
            }
        """
        # ★ P1-2: 每 20 条反馈后自动校准一次权重
        try:
            self.calibrate_weights_from_feedback()
        except Exception as _e:
            _log.debug("未知: %s", _e, exc_info=True)

        # ★ F-06a (2026-08-04): 计算层消费 F-00 引入的「无效断言」共用判据。
        #   根因(3层): ①原实现零断言+passed 时 assertion_rate 取满分 1.0，
        #   零断言用例反算出 ≈0.740 照样过闸（数析 Q7 实测）；②展示层与计算层
        #   各自一套 total_assertions==0 判据会口径打架；③PRD F-06a⑦ 要求
        #   全仓唯一判定 is_no_valid_assertion，禁止双重实现。
        #   修复: 无效断言 → confidence=0.0（结论不可用），不进入加权计算。
        try:
            from src.pipeline.utils.validation import is_no_valid_assertion

            if is_no_valid_assertion(intent_result):
                _log.warning(
                    "[ConfidenceCalc] case 无有效断言（结论不可用），confidence 置 0.0: %s",
                    intent_result.get("case_id", ""),
                )
                return {
                    "confidence": 0.0,
                    "level": "low",
                    "factors": {
                        "assertion_rate": 0.0,
                        "case_completeness": round(
                            sum(
                                1
                                for f in (
                                    "title",
                                    "case_type",
                                    "risk_level",
                                    "required_tracks",
                                )
                                if intent_result.get(f)
                            )
                            / 4.0,
                            3,
                        ),
                        "hard_verify_rate": 0.0,
                        "first_try_rate": 0.0,
                        "semantic_align": round(
                            intent_result.get("semantic_similarity", 0.0), 3
                        ),
                        "cache_hit_rate": 0.0,
                    },
                    "avg_rounds": 0.0,
                    "suggestion": "无有效断言，结论不可用（需人工确认）",
                    "no_valid_assertion": True,
                }
        except Exception as _e:
            # 判据组件不可用时不得阻塞（但不得因此放行 —— 下方缺席即满分
            # 修复仍生效，零断言不再给满分）
            _log.debug(
                "[ConfidenceCalc] is_no_valid_assertion 不可用: %s", _e, exc_info=True
            )

        steps = intent_result.get("steps", [])
        step_results = intent_result.get("step_results", [])
        passed = intent_result.get("passed", False)

        # ★ 2026-08-04: 统计 checks 验证结果（verify_checks step 的 _verify_result 三轨证据）——
        #   作为断言率兜底口径。根因(3层): ① guided 模式用例 explore_task 无操作步骤
        #   （需求为展示类），steps 无 assertions；② checks 有 3 个断言
        #   （element_exists/no_error/url_matches）全部 hard_verify PASS，结果存在
        #   _verify_result.ui/api/db.evidence；③ 计算层不统计 → assertion_rate=0.00 →
        #   真实 PASS 被误判"假PASS"。checks 全 PASS 是硬验证，必须计入断言率
        #   （不违背 F-06a 零断言 fail-closed——这里是有断言且全过）。
        # ★ : 真实性修复——跳过 verified=False 的轨（NOT_AVAILABLE）。
        #   根因: _build_verify_result 硬编码 api.passed=True 和 db.passed=True 产生虚假证据，
        #   v8 将此路径与 verdict 路径取 max，导致虚假证据被计入 assertion_rate。
        #   修复: 检查 _vr_data.get("verified", True)，verified=False 的轨跳过不计数。
        #   通用性: 跨系统/行业/页面 — 任何系统未验证的轨都跳过，不依赖特定系统类型。
        _checks_total = 0
        _checks_passed = 0
        for _sr in step_results:
            # ★ : 通用性修复——agent 路径的 _verify_result 也被计数
            #   根因: 原代码只检查 action=="verify_checks"，但 Agent 路径的 step_results
            #   使用 action=="agent"，导致 _checks_total 恒为 0，assertion_rate 退化到 0.0。
            #   修复: 只要 _verify_result 存在，无论 action 类型都计数。
            #   通用性: 跨系统/行业/页面 — 所有执行路径都有 _verify_result，不依赖 action 值。
            if _sr.get("_verify_result") and _sr.get("action") in ("verify_checks", "agent"):
                _vr = _sr["_verify_result"]
                for _track in ("ui", "api", "db"):
                    _vr_data = _vr.get(_track) or {}
                    # ★ v9: 跳过未验证的轨（NOT_AVAILABLE），不计入断言率
                    # ★ v11(2026-08-09): fail-safe——默认值改为 False
                    #   根因: .get("verified", True) 默认 True 意味着 verified 字段缺失时
                    #   轨被视为"已验证"——与设计意图完全相反。新系统（FinanceSystem/EduSystem）
                    #   的 browser_verification.py 路径返回的 verify_result 没有 verified 字段，
                    #   导致虚假证据全部计入 assertion_rate。
                    #   修复: 默认值改为 False，缺失 verified 字段的轨跳过不计数。
                    #   通用性: 跨系统/行业/页面 — fail-safe 原则，宁可不计也不虚高。
                    if not _vr_data.get("verified", False):
                        continue
                    _ev = _vr_data.get("evidence") or ""
                    # ★ evidence 可能是列表或 "; " 拼接的字符串（_run_checks_for_agent
                    #   return 时 join）——统一拆成条目列表，禁止逐字符遍历。
                    if isinstance(_ev, list):
                        _evs = [e for e in _ev if isinstance(e, str) and e.strip()]
                    elif isinstance(_ev, str):
                        _evs = [e.strip() for e in _ev.split("; ") if e.strip()]
                    else:
                        _evs = []
                    for _e in _evs:
                        _checks_total += 1
                        _checks_passed += 1 if _e.startswith("[PASS]") or bool(_vr_data.get("passed")) else 0

        # 1. ★ 断言通过率：优先从 verdict evidence_items 读取，退化到步骤级 assertions
        verdict = (
            intent_result.get("verdict") or intent_result.get("evidence_verdict") or {}
        )
        if isinstance(verdict, dict) and verdict.get("evidence_items"):
            total_assertions = len(verdict["evidence_items"])
            passed_assertions = sum(
                1
                for e in verdict["evidence_items"]
                if isinstance(e, dict) and e.get("verified", e.get("passed", False))
            )
            # ★ 2026-08-04: verdict 无有效断言时，融合 checks 验证结果（覆盖 guided 模式）
            if total_assertions == 0 and _checks_total > 0:
                total_assertions, passed_assertions = _checks_total, _checks_passed
            # ★ 2026-08-04: verdict evidence_items 存在但 verified 标记缺失/不全（guided
            #   模式 verdict 构建问题）时，用 checks 硬验证口径兜底取 max——
            #   checks 全部 hard_verify PASS 是比 verdict 标记更可靠的断言证据。
            if _checks_total > 0:
                _checks_rate = _checks_passed / _checks_total
                _verdict_rate = (
                    passed_assertions / total_assertions
                    if total_assertions > 0
                    else 0.0
                )
                assertion_rate = max(_verdict_rate, _checks_rate)
            else:
                # ★ F-06a (2026-08-04): 零断言不再给满分 —— 缺席即 0 分（fail-closed）。
                #   原实现 `1.0 if passed else 0.0` 使零断言用例 assertion 因子拿满额
                #   0.30，反算 confidence≈0.740 照样过闸（数析 Q7 实测）。
                assertion_rate = (
                    passed_assertions / total_assertions
                    if total_assertions > 0
                    else 0.0
                )
        else:
            # 退化：步骤级 assertions
            total_assertions = 0
            passed_assertions = 0
            sr_by_idx = {sr.get("step_idx"): sr for sr in step_results}
            for i, step in enumerate(steps):
                assertions = step.get("assertions", [])
                if assertions:
                    total_assertions += len(assertions)
                    ar = sr_by_idx.get(i)
                    if ar and ar.get("assertions_passed"):
                        passed_assertions += len(ar["assertions_passed"])
            # ★ 2026-08-04: 融合 checks 验证结果（verify_checks step 的三轨证据）
            total_assertions += _checks_total
            passed_assertions += _checks_passed
            # ★ F-06a: 同上，零断言 → 0 分（不再 `1.0 if passed else 0.0`）
            assertion_rate = (
                passed_assertions / total_assertions if total_assertions > 0 else 0.0
            )

        # 2. v3.0 用例完整度：从核心字段填充率 + checks[] 数量计算
        CORE_FIELDS = ["title", "case_type", "risk_level", "required_tracks"]
        filled = sum(1 for f in CORE_FIELDS if intent_result.get(f))
        case_completeness = filled / len(CORE_FIELDS)
        # 证据覆盖加成：有 checks[] 证据点 +0.1，但不超过 1.0
        # v3.0: 优先从 checks[] 读取，退化到 verification_plan（运行时）
        checks = intent_result.get("checks") or []
        if isinstance(checks, str):
            try:
                checks = json.loads(checks)
            except json.JSONDecodeError:
                checks = []
        ev_total = len(checks) if isinstance(checks, list) else 0
        if ev_total == 0:
            vp = intent_result.get("verification_plan") or {}
            if isinstance(vp, str):
                try:
                    vp = json.loads(vp)
                except json.JSONDecodeError:
                    vp = {}
            for track in ("ui", "api", "db"):
                items = (vp.get(track) or []) if isinstance(vp, dict) else []
                if isinstance(items, list):
                    ev_total += len(items)
        case_completeness = min(
            1.0,
            case_completeness + (0.1 if ev_total >= 3 else 0.05 if ev_total > 0 else 0),
        )

        # 3. 硬验证通过率
        hard_verified = sum(1 for sr in step_results if sr.get("_hard_verified"))
        hard_verify_rate = hard_verified / len(step_results) if step_results else 0.0

        # 4. 语义对齐度（需求-测试用例）
        semantic_align = intent_result.get("semantic_similarity", 0.0)
        if (
            semantic_align == 0.0
            and intent_result.get("req_text")
            and intent_result.get("intent_text")
        ):
            try:
                from src.extensions.semantic_index import similarity

                semantic_align = similarity(
                    intent_result["req_text"], intent_result["intent_text"]
                )
            except Exception as _e:
                _log.debug("verify + import: %s", _e, exc_info=True)

        # ★ v11(2026-08-09): 语义对齐 fallback——新系统无预置语义数据时
        #   根因: semantic_similarity 字段在整个 pipeline 中从未被赋值，新系统
        #   （FinanceSystem/EduSystem）无此数据，且 req_text/intent_text 亦为空，
        #   导致 semantic_align 恒为 0.00 → 所有置信度完全相同 0.830。
        #   修复: 当 semantic_align==0 且无 req_text/intent_text 时，根据 case_id
        #   与 req_id 的前缀匹配推断语义对齐度。TC-REQ-XXX-IYY 匹配 REQ-XXX 表示
        #   用例由该需求生成，语义对齐度至少为 0.5。
        #   通用性: 跨系统/行业/页面 — 不依赖任何外部数据源，纯算法推断。
        if semantic_align == 0.0:
            _case_id = intent_result.get("case_id", "")
            _req_id = intent_result.get("req_id", "")
            # 从 case_id 提取 req_id: TC-REQ-001-I01 → REQ-001
            if not _req_id and _case_id:
                import re as _re
                _m = _re.search(r'(REQ-\d+)', _case_id)
                if _m:
                    _req_id = _m.group(1)
            # 从 intent_result 中获取 req_id 列表
            _req_ids = intent_result.get("req_ids") or []
            if isinstance(_req_ids, str):
                _req_ids = [_req_ids]
            if _req_id and _req_id in _req_ids:
                semantic_align = 0.70  # 用例由对应需求生成，高对齐
            elif _req_id:
                semantic_align = 0.50  # 用例有需求来源但未在 req_ids 中
            elif _case_id:
                semantic_align = 0.30  # 有 case_id 但无法提取 req_id，中等对齐

        # ★ 通用修复(2026-07-21 v13): 位置偏差扣分
        #   当需求文档描述的位置与实际实现位置不一致时（location_mismatch=True），
        #   降低 semantic_align 分数，触发人工审核但不阻断执行。
        #   根因: 需求文档是大纲，实现以设计为准；需求位置≠实现位置是普遍现象
        #   通用性: 适用于所有"需求位置≠实现位置"的场景
        #   效果: semantic_align *= 0.6（扣40%），置信度降低但不阻断
        _control_validation = intent_result.get("_control_validation") or {}
        if isinstance(_control_validation, dict) and _control_validation.get(
            "location_mismatch"
        ):
            original_semantic = semantic_align
            semantic_align = semantic_align * 0.6  # 扣40%
            _log.info(
                "[ConfidenceCalc] 位置偏差扣分: semantic_align %.3f -> %.3f (location_mismatch=True)",
                original_semantic,
                semantic_align,
            )

        # 5. 经验复用命中率：区分操作缓存(op_cache)和业务能力缓存(capability_cache)
        # op_cache 是同项目同页面结构下的操作经验；capability_cache 是同项目同业务意图下的能力经验。
        op_cache_hits = int(
            intent_result.get("op_cache_hits", intent_result.get("cache_hits", 0)) or 0
        )
        op_cache_misses = int(
            intent_result.get("op_cache_misses", intent_result.get("cache_misses", 0))
            or 0
        )
        capability_hits = int(intent_result.get("capability_hits", 0) or 0)
        capability_misses = int(intent_result.get("capability_misses", 0) or 0)
        op_total = op_cache_hits + op_cache_misses
        capability_total = capability_hits + capability_misses
        op_cache_hit_rate = op_cache_hits / op_total if op_total > 0 else 0.0
        capability_hit_rate = (
            capability_hits / capability_total if capability_total > 0 else 0.0
        )
        if op_total > 0 and capability_total > 0:
            cache_hit_rate = round(
                op_cache_hit_rate * 0.4 + capability_hit_rate * 0.6, 3
            )
        elif capability_total > 0:
            cache_hit_rate = capability_hit_rate
        elif op_total > 0:
            cache_hit_rate = op_cache_hit_rate
        else:
            cache_hit_rate = 0.0

        # 6. ★ 首次尝试成功率（first_try_rate）——只有 rounds_needed<=1 的步骤才算"干净成功"
        # rounds_needed=0 → 结构化路径一次成功；=1 → AI 首轮成功；≥3 → 挣扎成功；=8 → 耗尽重试失败
        # ★ F-06a (2026-08-04): 消除「缺席即满分」—— ①零 step_results 不再
        #   `1.0 if passed else 0.0`（无执行数据却给满分）；②rounds_needed=None
        #   必须附带客观通过证据（_hard_verified / assertions_passed）才算成功，
        #   裸步骤无证据不再默认算成功（数析 Q7: 结构化主路径虚高真凶）。
        if step_results:
            ft_success = sum(
                1
                for sr in step_results
                if sr.get("first_try")
                or (
                    sr.get("rounds_needed") is None
                    and (sr.get("_hard_verified") or sr.get("assertions_passed"))
                )
                or (
                    isinstance(sr.get("rounds_needed"), int)
                    and sr["rounds_needed"] <= 1
                )
            )
            first_try_rate = ft_success / len(step_results)
            avg_rounds = sum(
                (sr.get("rounds_needed") or 0) for sr in step_results
            ) / len(step_results)
        else:
            first_try_rate = (
                0.0  # ★ F-06a: 无执行数据 → 0 分（原 `1.0 if passed else 0.0`）
            )
            avg_rounds = 0.0

        # ★ 重试惩罚：如果 avg_rounds > 2，额外降权
        retry_penalty = self._rounds_penalty(avg_rounds)
        # 把重试惩罚折算进 first_try_rate（避免重复计权）
        first_try_rate = round(first_try_rate * retry_penalty, 3)

        # 计算加权置信度
        factors = {
            "assertion_rate": round(assertion_rate, 3),
            "case_completeness": round(case_completeness, 3),
            "hard_verify_rate": round(hard_verify_rate, 3),
            "first_try_rate": first_try_rate,
            "semantic_align": round(semantic_align, 3),
            "cache_hit_rate": round(cache_hit_rate, 3),
            "op_cache_hit_rate": round(op_cache_hit_rate, 3),
            "capability_hit_rate": round(capability_hit_rate, 3),
        }

        weights = self.get_weights()
        confidence = sum(factors[k] * weights[k] for k in weights if k in factors)
        confidence = round(confidence, 3)

        # 分层
        if confidence >= 0.90:
            level = "high"
            suggestion = "自动通过"
        elif confidence >= 0.70:
            level = "medium"
            suggestion = "建议复核"
        else:
            level = "low"
            suggestion = "需人工确认"

        # 如果整体未通过，置信度封顶 0.69
        if not passed and confidence > 0.69:
            confidence = 0.69
            level = "low"
            suggestion = "执行失败，需人工确认"

        return {
            "confidence": confidence,
            "level": level,
            "factors": factors,
            "avg_rounds": round(avg_rounds, 1),
            "suggestion": suggestion,
        }
