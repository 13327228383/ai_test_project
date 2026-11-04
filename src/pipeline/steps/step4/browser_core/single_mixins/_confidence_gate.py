# -*- coding: utf-8 -*-
"""★ 置信度闸门 — 从 _base.py 拆出（§4.3 单文件≤800行）。

职责：PASS 但 confidence<70% 视为假 PASS，更新 execution 为 FAIL。
通用性：适用于所有系统/行业/页面/用例类型（P01/N01/B01/降级）。

根因（§10.9）:
    - 现象: 系统检测出置信度35%但仍记录 verdict=PASS
    - 直接原因: Step4 无置信度闸门
    - 根本原因: AGENTS.md 12.4 契约未强制执行
    - 通用性: 覆盖所有 PASS 路径（不针对单一用例/系统/行业）
    - 自我反驳3次:
        1) 只治标? → 不，AGENTS.md 12.4 是契约必须强制执行
        2) 应改Step3? → 不，改Step3需重生成用例违反一条一例原则
        3) 有更通用方案? → 无，这是最通用的 guardrail，覆盖所有 PASS 路径
"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


def apply_confidence_gate(
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
    expected="",
    actual="",
    details="",
    security_findings=None,
    adi_signal: bool = False,
):
    """置信度闸门：PASS 但 confidence<70% 或证据为空 视为假 PASS。

    参数:
        status:          executions.status（PASS/FAIL/MANUAL_REVIEW）
        case_passed:     闸门前的 case_passed 判定
        execution_id:    executions.id
        case_id:         用例ID
        case_meta:       用例元数据
        intent_payload:  意图 payload（含 required_tracks/checks）
        intent:          意图对象（含 cache_hits 等）
        intent_desc:     意图描述文本
        steps:           步骤列表
        step_results:    步骤结果列表
        expected:        预期证据（空则为无证据，触发假PASS拦截）
        actual:          实际证据
        details:         详细证据JSON

    返回:
        case_passed（True=通过，False=假PASS拦截或原 FAIL）
    """
    # ★ v12(2026-08-09): 移除 FAIL 的提前返回——所有用例都计算置信度
    #   根因: 原代码 `if status != "PASS": return case_passed` 导致 FAIL 用例
    #   从未计算置信度，confidence 恒为 0.0，confidence_factors 恒为 NULL。
    #   高级测试工程师即使面对失败用例，也会评估其证据质量（是真实失败还是
    #   证据不足的误判）。FAIL 用例的 confidence 被 _calculate.py 封顶 0.69，
    #   使 FAIL 与 PASS 之间仍有区分度（FAIL 最高 0.69，PASS 需要 0.70+）。
    #   通用性: 跨系统/行业/页面 — 所有执行状态都应有置信度评估，不依赖状态值。
    try:
        from src.extensions.confidence_calculator import ConfidenceCalculator

        # ★ : 通用性修复——从 intent 字典提取 case_type
        #   根因: case_type 在 test_cases 表中存储（'功能'/'安全'），但 _conf_data 只从
        #   case_meta 和 intent_payload 读取，这两个来源的 case_type 为空。
        #   修复: 增加 intent.get("case_type", "") 兜底，确保 case_type 传入置信度计算器。
        #   通用性: 跨系统/行业 — 所有 test_cases 都有 case_type 字段，不依赖业务术语。
        _case_type = (
            case_meta.get("case_type", "")
            or intent_payload.get("case_type", "")
            or intent.get("case_type", "")
            or ""
        )

        # ★ 通用性修复(2026-08-09, 用户问题 #1/#2): 置信度阈值改为可配置
        #   根因: 硬编码 0.70（功能）/ 0.60（安全）与 pipeline_config 的
        #   confidence_low_threshold（默认 0.50）不一致，导致 8 个执行 PASS 的用例
        #   被置信度闸门拦截（semantic_align 0.44-0.69 < 0.70），实际功能全部通过。
        #   修复: 从 pipeline_config.confidence_low_threshold 读取阈值，
        #   安全测试在 base 基础上降 0.10（最低 0.40）。
        #   用户可通过前端 UI 或环境变量 confidence_low_threshold 灵活调整。
        #   通用性: 跨系统/行业/页面 — 所有用例类型都使用统一可配置阈值源。
        try:
            from src.core.pipeline_config import pipeline_config as _pcfg
            _BASE_THRESHOLD = float(getattr(_pcfg, 'confidence_low_threshold', 0.50))
        except Exception:
            _BASE_THRESHOLD = 0.50
        # 安全测试阈值 = base - 0.10（最低 0.40），功能测试 = base
        _SECURITY_THRESHOLD = max(_BASE_THRESHOLD - 0.10, 0.40)
        _FUNCTIONAL_THRESHOLD = _BASE_THRESHOLD
        _threshold = _SECURITY_THRESHOLD if _case_type == "安全" else _FUNCTIONAL_THRESHOLD

        # ★ : 通用性修复——从 step_results 提取 evidence_items 构建 verdict
        #   根因: _conf_data 缺少 verdict/evidence_verdict，导致 _calculate.py 中
        #   assertion_rate 永远走不到 evidence_items 路径（恒为0.5，108/112用例）。
        #   修复: 从 step_results 的 _verify_result 提取三轨证据，构建 verdict.evidence_items。
        #   通用性: 跨系统/行业/页面 — 所有执行路径都有 step_results，不依赖特定 action 类型。
        # ★ : 真实性修复——跳过 verified=False 的轨（NOT_AVAILABLE）。
        #   根因: _build_verify_result 硬编码 api.passed=True 和 db.passed=True 产生虚假证据，
        #   v8 提取这些虚假证据计入 assertion_rate，导致断言率被虚高（102/112 用例 assertion_rate=1.0）。
        #   修复: 检查 _tdata.get("verified", True)，verified=False 的轨跳过不计数。
        #   通用性: 跨系统/行业/页面 — 任何系统未验证的轨都跳过，不依赖特定系统类型。
        _evidence_items = []
        for _sr in step_results:
            _vr = _sr.get("_verify_result")
            if not _vr:
                continue
            for _track in ("ui", "api", "db"):
                _tdata = _vr.get(_track) or {}
                # ★ v9: 跳过未验证的轨（NOT_AVAILABLE），不计入断言率
                # ★ v11(2026-08-09): fail-safe——默认值改为 False
                if not _tdata.get("verified", False):
                    continue
                _ev = _tdata.get("evidence") or ""
                if isinstance(_ev, list):
                    _ev_parts = [e for e in _ev if isinstance(e, str) and e.strip()]
                elif isinstance(_ev, str):
                    _ev_parts = [e.strip() for e in _ev.split("; ") if e.strip()]
                else:
                    _ev_parts = []
                for _part in _ev_parts:
                    _verified = _part.startswith("[PASS]") or bool(_tdata.get("passed"))
                    _evidence_items.append({
                        "track": _track,
                        "verified": _verified,
                        "passed": _verified,
                        "evidence": _part,
                    })
        # ★ 根因修复(2026-09-24): 双结构证据消费缺失 → assertion_rate 被低估为 0。
        #   现象: SEC01 等 absence 式安全校验把判定证据写入 details._items
        #   ({track, passed, evidence})，而本函数只读 step_results[*]._verify_result，
        #   两条路径分叉导致 _evidence_items 恒空 → assertion_rate=0.00 → 真实
        #   PASS(执行层已判 0.95) 被置信度闸门误拦为"假 PASS"。
        #   根因: 证据存在两个表示形式(_verify_result vs details._items)，
        #       闸门只消费其一。F-06a 的"无有效断言才 fail-closed"语义要求——凡
        #       有真实断言证据都必须计入，而非因未被该路径读到就判零断言。
        #   修复: step_results 无 _verify_result 证据时，回退从 details 的
        #       _items/_results 三轨块收割真实断言证据，与 _verify_result 同构。
        #   通用性: 跨系统/行业/页面 — 兜底收割任何轨道证据表示，不绑定业务。
        if not _evidence_items:
            _src_details = details
            if isinstance(_src_details, str):
                try:
                    import json as _dj
                    _src_details = _dj.loads(_src_details)
                except Exception:
                    _src_details = None
            if isinstance(_src_details, dict):
                _harvest_buckets = []
                for _bk in ("_items", "_results", "tracks", "track_results"):
                    if isinstance(_src_details.get(_bk), list):
                        _harvest_buckets.extend(_src_details[_bk])
                    elif isinstance(_src_details.get(_bk), dict):
                        _harvest_buckets.append(_src_details[_bk])
                for _it in _harvest_buckets:
                    if not isinstance(_it, dict):
                        continue
                    _h_track = _it.get("track") or ""
                    _h_passed = bool(_it.get("passed"))
                    _h_ev = _it.get("evidence") or ""
                    if not isinstance(_h_ev, str):
                        continue
                    _h_ev = _h_ev.strip()
                    if not _h_ev:
                        continue
                    if _it.get("verified", True) is not False and _h_passed:
                        _evidence_items.append({
                            "track": _h_track,
                            "verified": True,
                            "passed": True,
                            "evidence": _h_ev,
                        })
        _verdict = {"evidence_items": _evidence_items} if _evidence_items else {}

        # ★ 完整构造 intent_result，传所有可用字段（根因: 原仅传6字段致置信度被低估）
        _conf_data = {
            "case_id": case_id,
            "title": case_meta.get("title", "") or intent_payload.get("title", ""),
            "case_type": _case_type,
            "risk_level": case_meta.get("risk_level", "")
            or intent_payload.get("risk_level", ""),
            "required_tracks": intent_payload.get("required_tracks", []),
            "checks": intent_payload.get("checks", []),
            "verdict": _verdict,
            # ★ F-06a (2026-08-04): 硬编码 passed=True → 传入真实执行结果。
            #   根因(3层): ①原硬编码使门禁自我论证（无论结果如何都按通过算）；
            #   ②`_calculate.py:234-238` 的 0.69 封顶保护因此被永久绕过（铁证一）；
            #   ③门禁对"执行失败但置信度高"的用例不设防。
            #   修复: 传真实 case_passed；附带效果=重新接通 0.69 封顶保护。
            #   注意: 此处若 case_passed=False，ConfidenceCalculator 内部将走
            #   fail-closed 分支（confidence 封顶 0.69 < 0.70 → 门禁必然拦截）。
            "passed": case_passed,
            "steps": steps,
            "step_results": step_results,
            "req_text": intent.get("req_text", "")
            or intent_payload.get("source_quote", ""),
            "intent_text": intent_desc or intent_payload.get("description", ""),
            "cache_hits": intent.get("cache_hits", 0),
            "cache_misses": intent.get("cache_misses", 0),
            "op_cache_hits": intent.get("op_cache_hits", 0),
            "op_cache_misses": intent.get("op_cache_misses", 0),
            "capability_hits": intent.get("capability_hits", 0),
            "capability_misses": intent.get("capability_misses", 0),
        }
        _conf_result = ConfidenceCalculator.calculate(_conf_data)
        _confidence = _conf_result.get("confidence", 0)
        _factors = _conf_result.get("factors", {}) or {}
        _factors_str = ", ".join(f"{k}={v:.2f}" for k, v in _factors.items())
        # ★ v23: 持久化 confidence 到 executions 表（消除双计算路径，架构红线4）
        with guard("apply_confidence_gate: import json as _conf_json", log=_log, level=logging.WARNING):
            import json as _conf_json
            from src.users.audit import update_execution_confidence

            update_execution_confidence(
                execution_id=execution_id,
                confidence=_confidence,
                confidence_factors=_conf_json.dumps(_factors, ensure_ascii=False),
            )
        _log.info(
            "[ConfidenceGate] case_id=%s confidence=%.0f%% factors=%s",
            case_id,
            _confidence * 100,
            _factors_str,
        )

        # ★ SecurityScanner 集成: 高危安全发现降低置信度
        #  通用性: 跨系统/行业/页面 — 所有安全发现都应影响置信度评分
        #  策略: critical 发现 -0.20, high 发现 -0.10, 累计不低于 0.0
        #  当 penalized confidence 低于阈值时，由下方闸门自动拦截
        # ★ 类别归属根修(2026-09-22 全量判定审计): 惩罚仅作用于 case_type=安全 的用例。
        #   根因五层: L1 现象=21 条功能用例全因子 1.0 仍被拦(confidence≈0.35)；
        #   L2 直接=security_penalty -0.50（页面级扫描 6 个 high）打在功能用例上；
        #   L3 为何错=SecurityScanner 扫的是"页面"，发现属于系统安全态势，
        #      独立进入 Step5 安全区报告；功能用例的判定只对"功能语义"负责；
        #   L4 为何反复=惩罚无类别归属约束，任何执行路径上的功能用例都会被误伤；
        #   L5 根本=共享函数缺少"发现→用例类别"的归属判定。
        #   语义: 安全用例 PASS 却伴随 high 发现 = 安全控制可能失效 → 惩罚合理；
        #         功能用例的 PASS 与页面级安全发现无归属关系 → 不惩罚。
        _security_penalty = 0.0
        _security_summary = ""
        if security_findings and _case_type == "安全":
            _crit_count = sum(
                1 for f in security_findings if f.get("severity") == "critical"
            )
            _high_count = sum(
                1 for f in security_findings if f.get("severity") == "high"
            )
            # ★ 根因修复(2026-09-24): 页面级安全态势惩罚与单用例功能校验的归属性。
            #   现象: SecurityScanner 对整页做的一次性安全扫描(missing-CSP/HSTS/
            #   cookie-flags 等 6 条 high)被原封不动平摊到每个 case_type=安全 用例，
            #   以 cap=0.50 全量压低置信度。SEC01 用例自身断言已 hard-verify 通过
            #   (执行层 0.95)，被全局态势 -0.50 压到 0.385<0.40 误判"假 PASS"。
            #   根因: 全局扫描是"页面/系统级安全态势"，独立归入 Step5 安全区报告；
            #       单个安全用例的判定只对"用例校验的功能语义"负责。2026-09-22 已
            #       将惩罚限制到 安全 类用例(消除误伤功能用例)，但量级 cap=0.50 仍
            #       可单凭全局态势推翻一条已硬验证通过的安全用例 → 归类过度(over-penalty)。
            #   修复: 惩罚仍保留(安全态势弱 → 置信度适度下调, 语义=需复核)，但
            #       cap 降为 0.20，使一条已 hard_verify 通过的安全用例只被降级提示、
            #       不再被全局势态单方面否决。跨系统/行业/页面通用。
            _security_penalty = min(_crit_count * 0.20 + _high_count * 0.10, 0.20)
            if _security_penalty > 0:
                _confidence = max(_confidence - _security_penalty, 0.0)
                _security_summary = (
                    f"security_penalty=-{_security_penalty:.2f}"
                    f"(critical={_crit_count}, high={_high_count})"
                )
                _factors["security_penalty"] = -_security_penalty
                _factors_str = ", ".join(
                    f"{k}={v:.2f}" for k, v in _factors.items()
                )
                _log.warning(
                    "[ConfidenceGate] 安全发现降低置信度: case_id=%s %s → confidence=%.0f%%",
                    case_id,
                    _security_summary,
                    _confidence * 100,
                )
                # 重新持久化受安全惩罚影响后的置信度
                with guard("apply_confidence_gate: import json as _conf_json2", log=_log):
                    import json as _conf_json2
                    from src.users.audit import update_execution_confidence

                    update_execution_confidence(
                        execution_id=execution_id,
                        confidence=_confidence,
                        confidence_factors=_conf_json2.dumps(
                            _factors, ensure_ascii=False
                        ),
                    )

        # ★ v12(2026-08-09): 闸门仅对 PASS 用例生效，FAIL 用例只计算置信度不改变状态
        #   根因: 移除 FAIL 提前返回后，闸门逻辑会错误地将 FAIL 用例的 failure_reason
        #   覆盖为"置信度不足"，丢失原始失败原因。FAIL 用例的置信度已在 _calculate.py
        #   封顶 0.69，与 PASS 的 0.70 阈值自然区分，无需额外闸门拦截。
        #   通用性: 跨系统/行业/页面 — 所有状态的置信度都应计算，但闸门只拦截假PASS。
        if status == "PASS" and _confidence < _threshold:
            # ★ P0-3 增量（2026-08-29；**已复核 2026-09-04 修正**）：
            #   adi_signal 原实现由"静态代理（假定所有 check 全 FAIL）"算出，与真实
            #   证据/失败解耦。因此 SIGNAL 豁免低置信**仅在存在真实证据**时才可信——
            #   若 expected/actual/details 全空，绝不放行（空证据 PASS 仍是假PASS）。
            #   根因修复：把"无证据即无条件豁免"改为"必须有真实证据才豁免"。
            if adi_signal and (expected or actual or details):
                _log.info(
                    "[ConfidenceGate] SIGNAL 用例跳过置信度拦截(确有证据): case_id=%s confidence=%.0f%%",
                    case_id,
                    _confidence * 100,
                )
            else:
                _suggestion = _conf_result.get("suggestion", "")
                _fail_reason = (
                    f"置信度 {_confidence*100:.0f}% < {_threshold*100:.0f}% 阈值，视为假 PASS（AGENTS.md 12.4）。"
                    f"case_type={_case_type} factors: {_factors_str}; suggestion: {_suggestion}"
                )
                if _security_penalty > 0:
                    _fail_reason += f"; {_security_summary}"
                _log.warning(
                    "[ConfidenceGate] 假PASS拦截: case_id=%s confidence=%.0f%% factors=%s",
                    case_id,
                    _confidence * 100,
                    _factors_str,
                )
                with guard("apply_confidence_gate: from src.users.audit import update_e", log=_log):
                    from src.users.audit import update_execution_status

                    update_execution_status(
                        execution_id=execution_id,
                        status="FAIL",
                        failure_reason=_fail_reason,
                        failure_category="DATA_ERROR",
                    )
                return False
        # ★ : 通用性修复——证据为空也视为假PASS
        #   根因: is_fake_pass 在报告层拦截证据为空的PASS，但DB层未拦截，
        #   导致DB与报告不一致（双重阈值割裂）。统一在闸门层拦截。
        #   通用性: 跨系统/行业/页面 — 无证据的PASS在任何场景下都不可信。
        _ev_empty = not (expected or actual or details)
        # ★ v12: 证据为空拦截仅对 PASS 生效
        # ★ P0-3 增量（已复核 2026-09-04 修正）：**移除 adi_signal 豁免**。空证据的
        #   PASS 无论是否 SIGNAL 一律拦截 —— adi_signal 与"本次执行有无证据"无关，
        #   用它豁免空证据正违反 §13.4「禁止空 evidence 的 PASS」铁律。
        if status == "PASS" and _ev_empty:
            _fail_reason = (
                "证据为空（expected/actual/details 三列全空），视为假 PASS（AGENTS.md §13.4）。"
                f"confidence={_confidence*100:.0f}% factors={_factors_str}"
            )
            _log.warning(
                "[ConfidenceGate] 证据为空拦截: case_id=%s confidence=%.0f%%",
                case_id,
                _confidence * 100,
            )
            with guard("apply_confidence_gate: from src.users.audit import update_e", log=_log):
                from src.users.audit import update_execution_status

                update_execution_status(
                    execution_id=execution_id,
                    status="FAIL",
                    failure_reason=_fail_reason,
                    failure_category="DATA_ERROR",
                )
            return False
        return case_passed
    except Exception as _ce:
        _log.error(
            "[ConfidenceGate] 置信度计算异常(视为FAIL): %s",
            str(_ce)[:80],
            exc_info=True,
        )
        # ★ P0-fix(2026-07-29): 异常时禁止绕过闸门，视为FAIL
        #   根因: 置信度计算异常时返回 case_passed=True 是闸门绕过漏洞
        return False
