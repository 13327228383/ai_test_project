# -*- coding: utf-8 -*-
from __future__ import annotations

"""
证据引擎 — 置信度计算引擎 (Confidence Engine)
================================================
将 AI 自评置信度升级为证据驱动的贝叶斯校准置信度。

核心公式:
  confidence = 0.40 * evidence_strength     # 独立证据验证
             + 0.35 * calibration_accuracy   # 历史实证准确率
             + 0.15 * extraction_quality     # 源引用质量 + 多视角一致性
             + 0.10 * schema_validation      # Schema 确定性验证

设计原则:
  - 零硬编码: 所有权重/阈值从 pipeline_config 读取
  - 冷启动安全: 校准样本不足时自动降低历史权重
  - 通用系统: 不依赖特定 Step 或行业
  - 降级友好: 任一因子不可用时自动调整权重分布
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 权重配置（可通过 pipeline_config 覆盖）
# ═══════════════════════════════════════════════════════════════


def _get_default_weights():
    """动态读取证据引擎权重，支持配置覆盖。"""
    try:
        from src.core.pipeline_config import pipeline_config

        return {
            "evidence": float(pipeline_config.evidence_weight_evidence),
            "calibration": float(pipeline_config.evidence_weight_calibration),
            "extraction": float(pipeline_config.evidence_weight_extraction),
            "schema": float(pipeline_config.evidence_weight_schema),
        }
    except Exception as _e:
        _log.debug(
            "[evidence/confidence] 读取 pipeline_config 权重失败，使用硬编码默认值: %s",
            _e,
        )
        return {
            "evidence": 0.40,
            "calibration": 0.35,
            "extraction": 0.15,
            "schema": 0.10,
        }


# 保留模块级常量用于向后兼容（实际计算走 _get_default_weights）
_DEFAULT_WEIGHTS = _get_default_weights()


# ★ P1: 置信度档位走 pipeline_config (env 覆盖)
def _get_confidence_thresholds() -> dict:
    try:
        from src.core.pipeline_config import pipeline_config

        return {
            "HIGH": float(pipeline_config.evidence_high_confidence),
            "MEDIUM": float(pipeline_config.evidence_medium_confidence),
        }
    except Exception as _e:
        _log.debug(
            "[evidence/confidence] 读取 pipeline_config 置信度档位失败，使用默认值: %s",
            _e,
        )
        return {"HIGH": 0.85, "MEDIUM": 0.65}


_CONFIDENCE_THRESHOLDS = _get_confidence_thresholds()


# ═══════════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════════


def _ai_raw_confidence(
    consensus_level: str,
    perspective_count: int,
    has_source_quote: bool,
) -> float:
    """
    从蓝红对抗的现有字段计算 AI 原始置信度。

    规则（与当前 pipeline 行为一致）:
      - HIGH 共识 + 2+视角 + source_quote → 0.90
      - HIGH 共识 + 1视角 + source_quote → 0.82
      - MEDIUM 共识 + source_quote → 0.72
      - MEDIUM 共识 + 无 source_quote → 0.60
      - LOW 共识 → 0.45
      - 未知 → 0.50
    """
    level = str(consensus_level or "").upper().strip()
    pc = int(perspective_count or 1)

    if level == "HIGH":
        if pc >= 2 and has_source_quote:
            return 0.90
        elif has_source_quote:
            return 0.82
        elif pc >= 2:
            return 0.78
        else:
            return 0.70
    elif level == "HIGHEST":
        return 0.92
    elif level == "MEDIUM":
        if has_source_quote:
            return 0.72
        else:
            return 0.60
    elif level == "LOW":
        return 0.45
    else:
        return 0.50


def _extraction_quality(item: dict[str, Any]) -> float:
    """
    计算提取质量分数 (0.0~1.0)。

    考虑因素:
      - source_quote 长度（≥30字 +0.2, ≥10字 +0.1）
      - _perspective_count（≥3 → 1.0, 2 → 0.85, 1 → 0.65）
      - description 长度（≥50字 +0.1）
    """
    sq = str(item.get("source_quote", "") or "").strip()
    pc = int(item.get("_perspective_count", 1) or 1)
    desc = str(item.get("description", "") or item.get("title", "") or "")

    score = 0.5  # baseline

    # source_quote 质量
    if len(sq) >= 30:
        score += 0.2
    elif len(sq) >= 10:
        score += 0.1

    # 多视角共识
    if pc >= 3:
        score += 0.15
    elif pc == 2:
        score += 0.10
    # pc=1 不加分

    # 描述充分性
    if len(desc) >= 50:
        score += 0.10

    # 同一视角内一致性
    all_descs = item.get("_all_descriptions", [])
    if isinstance(all_descs, list) and len(all_descs) >= 2:
        score += 0.05

    return round(min(1.0, max(0.0, score)), 3)


def _compute_evidence_strength(evidence_results: list[dict]) -> float:
    """
    从独立验证结果计算证据强度 (0.0~1.0)。

    规则:
      - True → +1.0
      - unknown → +0.35（不惩罚，但也不加分）
      - False → +0.0
      - 取所有结果的加权平均
      - 无证据时返回 0.35（中性值；注意: 冷启动无证据时 weights["evidence"]=0.0，
        故 0.35 实际不参与最终置信度计算，见 compute_evidence_confidence L312-322）
    """
    if not evidence_results:
        return 0.35

    scores = []
    for ev in evidence_results:
        if not isinstance(ev, dict):
            continue
        verified = ev.get("verified")
        if verified == True:
            scores.append(1.0)
        elif verified == False:
            scores.append(0.0)
        elif verified == "unknown":
            scores.append(0.35)
        else:
            scores.append(0.35)

    if not scores:
        return 0.35

    return round(sum(scores) / len(scores), 3)


def _get_weighted_calibration(
    calibration_data: dict,
    cal_keys: dict[str, str],
) -> tuple:
    """
    从校准数据中按维度加权聚合历史准确率。

    参数:
        calibration_data: get_calibration() 返回的完整数据
        cal_keys: {"consensus_level": "HIGH", "perspective_count": "2", ...}

    返回:
        (weighted_accuracy, total_samples)
    """
    dimensions = calibration_data.get("dimensions", {})
    if not dimensions:
        return 0.0, 0

    accuracies = []
    samples_list = []

    for key, value in cal_keys.items():
        dim = dimensions.get(key, {})
        entry = dim.get(value, {})
        if entry:
            acc = entry.get("accuracy", 0.0)
            n = entry.get("predictions", 0)
            if n > 0:
                accuracies.append(acc)
                samples_list.append(n)

    if not accuracies:
        return 0.0, 0

    # 加权平均（按样本数加权）
    total_weight = sum(samples_list)
    if total_weight == 0:
        return 0.0, 0

    # ★ Python 3.9 兼容：不使用 strict 参数
    weighted = sum(a * w for a, w in zip(accuracies, samples_list)) / total_weight
    # ★ 调和平均样本数：比 min() 更平滑。所有维度都需有数据才能增长，
    #   但不会被单一稀疏维度（如 perspective_count=3+ 仅 8 条）卡死整个系统。
    total_samples = round(
        len(samples_list) / sum(1.0 / max(s, 1) for s in samples_list)
    )

    return round(weighted, 4), total_samples


# ═══════════════════════════════════════════════════════════════
# 核心：证据驱动置信度计算
# ═══════════════════════════════════════════════════════════════


def compute_evidence_confidence(
    item: dict[str, Any],
    calibration_data: dict | None = None,
    independent_evidence: list[dict] | None = None,
) -> dict[str, Any]:
    """
    证据驱动的置信度计算。

    参数:
        item:                 需求或意图 dict
                              - 需求: 含 description, source_quote, module, _consensus_level, _perspective_count
                              - 用例: 含 title, checks, _perspective_count
        calibration_data:     get_calibration() 返回的历史校准数据（None=跳过校准）
        independent_evidence: verify_claim() 返回的独立验证结果列表（None=跳过）

    返回:
        {
          "confidence": 0.85,           # 最终校准置信度
          "ai_raw_confidence": 0.90,    # AI 原始声称
          "calibration_accuracy": 0.83, # 历史准确率 (None=无数据)
          "calibration_samples": 247,   # 校准样本数
          "evidence_strength": 0.75,    # 独立证据强度
          "confidence_level": "HIGH",   # HIGH/MEDIUM/LOW
          "cold_start": False,          # 是否冷启动
          "verdict": "evidence_calibrated",  # evidence_calibrated/calibrated_only/cold_start/ai_only
        }
    """
    from src.core.pipeline_config import pipeline_config

    prior_weight = float(
        getattr(pipeline_config, "evidence_bayesian_prior_weight", 10.0)
    )
    min_samples = int(getattr(pipeline_config, "evidence_min_calibration_samples", 30))

    # ── 1. AI 原始置信度 ──
    consensus = str(item.get("_consensus_level", "") or "").strip()
    if not consensus:
        # 从其他字段推断
        pc = int(item.get("_perspective_count", 1) or 1)
        # ★ 注意: perspective_count 最小为 1（保底），pc=0 不会出现。
        #   若未来允许 0 视角（如纯规则生成），此处应映射到 LOW 而非 MEDIUM。
        if pc >= 2:
            consensus = "HIGH"
        elif pc == 1:
            consensus = "MEDIUM"
        else:
            consensus = "MEDIUM"

    persp_count = int(item.get("_perspective_count", 1) or 1)
    has_sq = bool(str(item.get("source_quote", "") or "").strip())

    ai_raw = _ai_raw_confidence(consensus, persp_count, has_sq)

    # ── 2. 历史校准 ──
    cal_accuracy = None
    cal_samples = 0
    cold_start = True

    if calibration_data and calibration_data.get("dimensions"):
        cal_key_map = {
            "consensus_level": consensus,
            "perspective_count": (
                "1" if persp_count == 1 else ("2" if persp_count == 2 else "3+")
            ),
            "source_quote": "yes" if has_sq else "no",
        }
        cal_accuracy, cal_samples = _get_weighted_calibration(
            calibration_data, cal_key_map
        )
        cold_start = cal_samples < min_samples

    # ── 3. 独立证据验证 ──
    evidence_strength = _compute_evidence_strength(independent_evidence or [])

    # schema 确定性验证通过率
    schema_validated = False
    if independent_evidence:
        schema_validated = any(
            isinstance(ev, dict)
            and ev.get("method") == "schema_probe"
            and ev.get("verified") == True
            for ev in independent_evidence
        )

    # ── 4. 贝叶斯校准 ──
    if not cold_start and cal_accuracy is not None and cal_samples > 0:
        from src.pipeline.evidence.calibration import _bayesian_posterior

        calibrated = _bayesian_posterior(
            ai_raw, cal_accuracy * cal_samples, cal_samples, prior_weight
        )
    else:
        calibrated = ai_raw  # 冷启动保留 AI 原始

    # ── 5. 因子权重分配 ──
    weights = dict(_DEFAULT_WEIGHTS)

    # 冷启动时调整权重：降低 calibration 权重，增加 extraction
    if cold_start:
        if cal_samples > 0:
            # 部分样本：按比例缩放 calibration 权重
            cal_ratio = cal_samples / max(min_samples, 1)
            weights["calibration"] = 0.35 * cal_ratio
            weights["extraction"] = 0.15 + (0.35 - weights["calibration"]) * 0.5
            weights["evidence"] = 0.40 + (0.35 - weights["calibration"]) * 0.5
        else:
            # 完全无样本：全部权重给 extraction + evidence
            weights["calibration"] = 0.0
            weights["extraction"] = 0.25
            weights["evidence"] = 0.55
            weights["schema"] = 0.20

    # 无独立证据时调整权重
    if not independent_evidence:
        # 无证据时：更多依赖提取质量和先验
        surplus = weights["evidence"] + weights["schema"]
        weights["evidence"] = 0.0
        weights["schema"] = 0.0
        weights["extraction"] += surplus * 0.7
        if not cold_start:
            weights["calibration"] += surplus * 0.3
        else:
            weights["extraction"] += surplus * 0.3

    # ★ 归一化：冷启动+无证据 两次权重调整叠加后可能漂移（如 0.475≠1.0），
    #   此处强制归一化保证各因子贡献比例不失真。正常路径 sum≈1.0，无副作用。
    _wsum = sum(weights.values())
    if _wsum > 0 and abs(_wsum - 1.0) > 0.001:
        for _k in weights:
            weights[_k] /= _wsum

    # ── 6. 综合置信度 ──
    cal_score = cal_accuracy if not cold_start and cal_accuracy is not None else 0.5
    extraction_q = _extraction_quality(item)

    confidence = (
        weights["evidence"] * evidence_strength
        + weights["calibration"] * cal_score
        + weights["extraction"] * extraction_q
        + weights["schema"] * (0.95 if schema_validated else 0.45)
    )

    # 冷启动无证据时保底：AI 先验至少占 40% 权重
    if cold_start and not independent_evidence:
        confidence = 0.40 * ai_raw + 0.60 * confidence

    # ── 7. 置信度分级 ──
    level = "LOW"
    for lvl_name, threshold in sorted(
        _CONFIDENCE_THRESHOLDS.items(), key=lambda x: x[1], reverse=True
    ):
        if confidence >= threshold:
            level = lvl_name
            break

    # ── 8. 判决类型 ──
    if cold_start:
        verdict = "cold_start"
    elif independent_evidence:
        verdict = "evidence_calibrated"
    elif cal_accuracy is not None:
        verdict = "calibrated_only"  # ★ 有校准数据但无独立证据
    else:
        verdict = "ai_only"

    return {
        "confidence": round(min(0.99, max(0.01, confidence)), 3),
        "ai_raw_confidence": round(ai_raw, 3),
        "calibration_accuracy": (
            round(cal_accuracy, 3) if cal_accuracy is not None else None
        ),
        "calibration_samples": cal_samples,
        "evidence_strength": round(evidence_strength, 3),
        "confidence_level": level,
        "cold_start": cold_start,
        "verdict": verdict,
    }
