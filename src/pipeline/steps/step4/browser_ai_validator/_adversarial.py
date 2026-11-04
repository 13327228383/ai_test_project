# -*- coding: utf-8 -*-
"""
Step 4 红队对抗验证 (Adversarial Result Verification)
======================================================
对执行结果进行独立对抗审查，不修改原执行引擎。

三个验证器（全部使用 config.verification_model）:
  Verifier A: 证据一致性 — 执行证据是否支撑 pass/fail 判定？有无矛盾？
  Verifier B: 误判审查   — passed 结果是否可能漏报（边界/异常/超时）？
  Verifier C: 可靠性评估 — 执行过程是否有异常信号（重试/超时/元素未找到）？

★ 零硬编码：所有模型从 config 读取。
★ 异常安全：任何验证器失败不阻断主流程，降级跳过。
"""

import json
import logging
from typing import Any
from concurrent.futures import ThreadPoolExecutor

_log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 可配置阈值
# ═══════════════════════════════════════════════════════════════

_MAX_REVIEW_SAMPLES = 20  # 最多送 AI 审查的样本数
_MIN_REVIEW_SAMPLES = 3  # 最少样本数才触发 AI 审查


# ★ P1: 快速通道阈值走 pipeline_config (env 覆盖)
def _get_fast_reliability_threshold() -> float:
    try:
        from src.core.pipeline_config import pipeline_config

        return float(pipeline_config.adversarial_fast_reliability)
    except Exception as _e:
        _log.debug(
            "[_adversarial] _get_fast_reliability_threshold 异常: %s", _e, exc_info=True
        )
        return 0.85


_FAST_RELIABILITY_THRESHOLD = _get_fast_reliability_threshold()
_SLOW_STEP_SECONDS = 8.0  # 步骤超过此秒数视为慢（异常信号）


# ═══════════════════════════════════════════════════════════════
# Verifier A: 证据一致性
# ═══════════════════════════════════════════════════════════════

_EVIDENCE_SYSTEM_PROMPT = (
    "你是执行结果质检专家。请审查以下测试用例的执行结果，"
    "判断证据是否充分支撑 pass/fail 判定。\n\n"
    "## 审查维度\n"
    "1. passed 但无任何证据条目 → 可能是空执行，疑似漏报\n"
    "2. failed 但所有步骤都 passed → 证据矛盾，可能是硬校验假阳性\n"
    "3. passed 但步骤耗时异常短 (<0.1s) → 可能是跳过执行\n"
    "4. 步骤描述与 intent_title 不匹配 → 可能执行了错误的意图\n\n"
    "## 输出格式（严格 JSON，不要其他文字）\n"
    "{\n"
    '  "verdict": "consistent|inconsistent|suspicious",\n'
    '  "consistency_score": 0.0~1.0,\n'
    '  "issues": [\n'
    '    {"case_id": "TC-XXX", "issue": "问题描述", "severity": "high|medium|low"}\n'
    "  ],\n"
    '  "confidence": "high|medium|low"\n'
    "}"
)


def _review_evidence_consistency(
    sample_results: list[dict],
    sample_intents: dict[str, dict],
    verification_model: str,
) -> dict[str, Any]:
    """验证器 A: 证据一致性审查。"""
    from src.ai.ai_client import ai_brain

    # 构建摘要
    items = []
    for r in sample_results:
        iid = r.get("case_id", "?")
        intent_info = sample_intents.get(iid, {})
        items.append(
            {
                "case_id": iid,
                "intent_title": intent_info.get("title", ""),
                "passed": r.get("passed", False),
                "step_count": len(r.get("step_results", [])),
                "step_summary": [
                    {
                        "desc": s.get("description", "")[:60],
                        "passed": s.get("passed", False),
                        "elapsed": s.get("elapsed", 0),
                        "error": s.get("error", "")[:80],
                    }
                    for s in r.get("step_results", [])[:3]
                ],
            }
        )

    user_msg = (
        "## 执行结果样本 (%d 条)\n%s\n\n"
        "请审查以上执行结果，判断 pass/fail 判定是否被证据充分支撑。"
    ) % (len(items), json.dumps(items, ensure_ascii=False, indent=2))

    result = ai_brain._call_api(
        system_prompt=_EVIDENCE_SYSTEM_PROMPT,
        user_message=user_msg,
        temperature=0.1,
        max_tokens=1024,
        caller_name="红队-证据一致性",
        task_type="verification",
        force_cloud=True,
        cloud_model=verification_model,
    )

    if not result.get("success"):
        return {
            "verdict": "unknown",
            "consistency_score": 0.5,
            "_error": result.get("error", "?")[:120],
        }

    parsed = ai_brain._parse_json_response(result["data"])
    if parsed.get("success"):
        return parsed["data"]
    return {"verdict": "unknown", "consistency_score": 0.5, "_error": "JSON解析失败"}


# ═══════════════════════════════════════════════════════════════
# Verifier B: 误判审查
# ═══════════════════════════════════════════════════════════════

_FALSE_POSITIVE_SYSTEM_PROMPT = (
    "你是测试结果审计专家。请审查以下 passed 的测试用例，"
    "判断是否存在误判（实际应为 fail 但被判为 pass）。\n\n"
    "## 审查维度\n"
    "1. 是否有步骤虽然 passed 但描述暗示了潜在问题（如超时后继续、元素替换后通过）？\n"
    "2. 意图标题涉及边界/异常/安全场景但判为 pass — 是否真正验证了异常行为？\n"
    "3. 步骤耗时接近超时阈值 — 是否只是侥幸通过？\n"
    "4. 仅依赖 DOM 文本但未验证 API/DB — 数据是否正确落库？\n\n"
    "## 输出格式（严格 JSON，不要其他文字）\n"
    "{\n"
    '  "verdict": "clean|suspicious|false_positive",\n'
    '  "false_positive_rate": 0.0~1.0,\n'
    '  "suspicious_intents": [\n'
    '    {"case_id": "TC-XXX", "reason": "疑似误判原因", "confidence": "high|medium|low"}\n'
    "  ],\n"
    '  "confidence": "high|medium|low"\n'
    "}"
)


def _review_false_positives(
    passed_results: list[dict],
    passed_intents: dict[str, dict],
    verification_model: str,
) -> dict[str, Any]:
    """验证器 B: 误判审查 — 检查 passed 结果是否可能是 false positive。"""
    from src.ai.ai_client import ai_brain

    if not passed_results:
        return {
            "verdict": "clean",
            "false_positive_rate": 0.0,
            "suspicious_intents": [],
            "confidence": "high",
        }

    items = []
    for r in passed_results:
        iid = r.get("case_id", "?")
        intent_info = passed_intents.get(iid, {})
        items.append(
            {
                "case_id": iid,
                "intent_title": intent_info.get("title", ""),
                "case_type": intent_info.get("case_type", ""),
                "risk_level": intent_info.get("risk_level", ""),
                "step_count": len(r.get("step_results", [])),
                "max_step_elapsed": max(
                    (s.get("elapsed", 0) for s in r.get("step_results", [])), default=0
                ),
            }
        )

    user_msg = (
        "## Passed 意图 (%d 条)\n%s\n\n" "请审查以上 passed 结果，识别疑似误判。"
    ) % (len(items), json.dumps(items, ensure_ascii=False, indent=2))

    result = ai_brain._call_api(
        system_prompt=_FALSE_POSITIVE_SYSTEM_PROMPT,
        user_message=user_msg,
        temperature=0.1,
        max_tokens=1024,
        caller_name="红队-误判审查",
        task_type="verification",
        force_cloud=True,
        cloud_model=verification_model,
    )

    if not result.get("success"):
        return {
            "verdict": "unknown",
            "false_positive_rate": 0.0,
            "_error": result.get("error", "?")[:120],
        }

    parsed = ai_brain._parse_json_response(result["data"])
    if parsed.get("success"):
        return parsed["data"]
    return {"verdict": "unknown", "false_positive_rate": 0.0, "_error": "JSON解析失败"}


# ═══════════════════════════════════════════════════════════════
# Verifier C: 可靠性评估（结构化，不调 AI）
# ═══════════════════════════════════════════════════════════════


def _assess_reliability(execution_results: list[dict]) -> dict[str, Any]:
    """验证器 C: 可靠性评估 — 纯结构化分析，不调 AI。

    检查信号:
      - 重试迹象（同 intent 多步骤失败后成功）
      - 超时（步骤耗时 > 阈值）
      - 元素未找到（错误消息中包含相关关键词）
      - DOM 突变（步骤间 DOM 计数变化异常）
    """
    total = len(execution_results)
    if total == 0:
        return {
            "verdict": "unknown",
            "reliability_score": 0.0,
            "signals": [],
            "confidence": "low",
        }

    signals = []
    per_intent_scores = {}

    for r in execution_results:
        iid = r.get("case_id", "?")
        step_results = r.get("step_results", [])
        if not step_results:
            per_intent_scores[iid] = 0.5
            continue

        step_count = len(step_results)
        slow_steps = sum(
            1 for s in step_results if s.get("elapsed", 0) > _SLOW_STEP_SECONDS
        )
        failed_then_passed = 0
        retry_keywords = 0
        for s in step_results:
            err = s.get("error", "").lower()
            if any(
                kw in err
                for kw in (
                    "retry",
                    "timeout",
                    "not found",
                    "找不到",
                    "超时",
                    "重试",
                    "element not",
                    "stale",
                )
            ):
                retry_keywords += 1

        # 失败后仍有 passed 步骤 → 重试/恢复迹象
        has_failure = any(not s.get("passed", True) for s in step_results)
        has_late_pass = step_count > 1 and any(
            s.get("passed", True) for s in step_results[1:]
        )
        if has_failure and has_late_pass:
            failed_then_passed = 1

        # 计分: 完美执行 = 1.0，扣分项叠加（最低 0.2）
        score = 1.0
        score -= min(0.4, slow_steps * 0.1)  # 慢步骤 -0.1 每个
        score -= min(0.3, failed_then_passed * 0.2)  # 失败后恢复 -0.2
        score -= min(0.3, retry_keywords * 0.15)  # 重试关键词 -0.15 每个
        score = max(0.2, score)

        per_intent_scores[iid] = round(score, 3)

        if score < 0.7:
            sig_parts = []
            if slow_steps:
                sig_parts.append(
                    "%d个慢步骤(>%.0fs)" % (slow_steps, _SLOW_STEP_SECONDS)
                )
            if failed_then_passed:
                sig_parts.append("有失败后恢复")
            if retry_keywords:
                sig_parts.append("含%d个异常关键词" % retry_keywords)
            signals.append(
                {
                    "case_id": iid,
                    "reliability": score,
                    "signals": sig_parts,
                }
            )

    avg_score = (
        sum(per_intent_scores.values()) / len(per_intent_scores)
        if per_intent_scores
        else 0.0
    )

    verdict = "reliable"
    if avg_score < 0.5:
        verdict = "unreliable"
    elif avg_score < 0.7:
        verdict = "degraded"

    _log.info(
        "[红队-C] 可靠性评估: avg=%.2f verdict=%s signals=%d",
        avg_score,
        verdict,
        len(signals),
    )

    return {
        "verdict": verdict,
        "reliability_score": round(avg_score, 3),
        "signals": signals[:15],
        "per_intent_scores": per_intent_scores,
        "confidence": "high" if total >= 10 else "medium",
    }


# ═══════════════════════════════════════════════════════════════
# 公开入口
# ═══════════════════════════════════════════════════════════════


def adversarial_review_results(
    execution_results: list[dict],
    candidates: list[dict] = None,
) -> dict[str, Any]:
    """
    Step 4 红队对抗验证 — 对执行结果进行独立对抗审查。

    参数:
        execution_results: _execute_one_intent 返回的 per-intent 结果列表
                           每项: {case_id, passed, step_results: [{step_idx, description, passed, error, elapsed}]}
        candidates:        原始测试用例列表（用于获取 title/type/risk_level）
                           每项: {case_id, case_meta: {title, case_type, risk_level}, case_description}

    返回:
        {
          "success": True/False,
          "reliability_score": 0.0~1.0,        # 综合可靠性评分
          "evidence_consistency": {...},         # Verifier A 报告
          "false_positive_review": {...},        # Verifier B 报告
          "reliability_assessment": {...},       # Verifier C 报告（结构化）
          "anomalies": [...],                    # 汇总异常
          "verdicts": {"reliable": N, "flaky": M, "unreliable": K},
        }
    """
    from src.core.config_loader import config

    verification_model = config.verification_model
    if not verification_model:
        verification_model = config.ai_model

    if not execution_results:
        return {
            "success": False,
            "error": "无执行结果可审查",
            "reliability_score": 0.0,
        }

    total = len(execution_results)
    passed_results = [r for r in execution_results if r.get("passed")]
    failed_results = [r for r in execution_results if not r.get("passed")]

    # 构建 case_id → candidate 映射
    intent_map: dict[str, dict] = {}
    if candidates:
        for c in candidates:
            iid = c.get("case_id", "")
            meta = (
                c.get("case_meta", {}) if isinstance(c.get("case_meta"), dict) else {}
            )
            intent_map[iid] = {
                "title": meta.get("title", c.get("case_description", "")),
                "case_type": meta.get("case_type", ""),
                "risk_level": meta.get("risk_level", ""),
            }

    _log.info(
        "[红队 Step4] 审查 %d 条执行结果 (passed=%d failed=%d)...",
        total,
        len(passed_results),
        len(failed_results),
    )

    # ── Verifier C: 可靠性评估（结构化，先执行，免费）──
    reliability = _assess_reliability(execution_results)

    # ── 检查是否需要 AI 审查 ──
    avg_reliability = reliability.get("reliability_score", 0.5)
    needs_ai_review = total >= _MIN_REVIEW_SAMPLES and (
        avg_reliability < _FAST_RELIABILITY_THRESHOLD or len(failed_results) > 0
    )

    evidence_report = {
        "verdict": "skipped",
        "consistency_score": 1.0,
        "issues": [],
        "_note": "快速通道: 可靠性=%.2f ≥ %.2f 且无失败"
        % (avg_reliability, _FAST_RELIABILITY_THRESHOLD),
    }
    fp_report = {
        "verdict": "skipped",
        "false_positive_rate": 0.0,
        "suspicious_intents": [],
        "_note": "快速通道",
    }

    if needs_ai_review:
        _log.info(
            "[红队 Step4] 需要 AI 深度审查 (可靠性=%.2f < %.2f 或 %d 失败)",
            avg_reliability,
            _FAST_RELIABILITY_THRESHOLD,
            len(failed_results),
        )

        # 抽样: 优先失败 + 可靠性最低的 passed
        review_sample = list(failed_results[: _MAX_REVIEW_SAMPLES // 2])
        if len(review_sample) < _MAX_REVIEW_SAMPLES:
            # 按可靠性升序取 passed
            sorted_passed = sorted(
                passed_results,
                key=lambda r: reliability.get("per_intent_scores", {}).get(
                    r.get("case_id", ""), 0.5
                ),
            )
            remaining = _MAX_REVIEW_SAMPLES - len(review_sample)
            review_sample.extend(sorted_passed[:remaining])

        sample_intents = {
            r.get("case_id", ""): intent_map.get(r.get("case_id", ""), {})
            for r in review_sample
        }

        # 并行执行 Verifier A + B
        with ThreadPoolExecutor(max_workers=2) as executor:
            f_evidence = executor.submit(
                _review_evidence_consistency,
                review_sample,
                sample_intents,
                verification_model,
            )
            f_fp = executor.submit(
                _review_false_positives,
                passed_results[:_MAX_REVIEW_SAMPLES],
                {
                    r.get("case_id", ""): intent_map.get(r.get("case_id", ""), {})
                    for r in passed_results[:_MAX_REVIEW_SAMPLES]
                },
                verification_model,
            )

            try:
                evidence_report = f_evidence.result()
            except Exception as e:
                _log.warning("[红队-A] 证据一致性审查异常: %s", str(e)[:120])
                evidence_report = {
                    "verdict": "error",
                    "consistency_score": 0.5,
                    "_error": str(e)[:200],
                }

            try:
                fp_report = f_fp.result()
            except Exception as e:
                _log.warning("[红队-B] 误判审查异常: %s", str(e)[:120])
                fp_report = {
                    "verdict": "error",
                    "false_positive_rate": 0.0,
                    "_error": str(e)[:200],
                }

    # ── 汇总 ──
    anomalies = []
    # 从 evidence_report
    for issue in evidence_report.get("issues", []):
        anomalies.append(
            {
                "source": "evidence_consistency",
                "case_id": issue.get("case_id", "?"),
                "issue": issue.get("issue", ""),
                "severity": issue.get("severity", "low"),
            }
        )
    # 从 fp_report
    for si in fp_report.get("suspicious_intents", []):
        anomalies.append(
            {
                "source": "false_positive",
                "case_id": si.get("case_id", "?"),
                "issue": si.get("reason", ""),
                "severity": "medium",
            }
        )
    # 从 reliability
    for sig in reliability.get("signals", []):
        anomalies.append(
            {
                "source": "reliability",
                "case_id": sig.get("case_id", "?"),
                "issue": "; ".join(sig.get("signals", [])),
                "severity": "high" if sig.get("reliability", 0) < 0.4 else "medium",
            }
        )

    # 分类 verdicts
    per_scores = reliability.get("per_intent_scores", {})
    verdicts = {"reliable": 0, "flaky": 0, "unreliable": 0}
    for iid, score in per_scores.items():
        if score >= 0.7:
            verdicts["reliable"] += 1
        elif score >= 0.4:
            verdicts["flaky"] += 1
        else:
            verdicts["unreliable"] += 1

    _log.info(
        "[红队 Step4] 完成: 可靠性=%.2f 证据=%.2f 误判率=%.2f | reliable=%d flaky=%d unreliable=%d",
        avg_reliability,
        evidence_report.get("consistency_score", 0),
        fp_report.get("false_positive_rate", 0),
        verdicts["reliable"],
        verdicts["flaky"],
        verdicts["unreliable"],
    )

    return {
        "success": True,
        "reliability_score": avg_reliability,
        "evidence_consistency": evidence_report,
        "false_positive_review": fp_report,
        "reliability_assessment": reliability,
        "anomalies": anomalies,
        "verdicts": verdicts,
    }
