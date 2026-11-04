# -*- coding: utf-8 -*-
"""
health.fetchers._format — 健康度数据格式化层
==============================================
封装置信度标签、标签映射和摘要响应构建逻辑。
"""

import logging

_log = logging.getLogger(__name__)


def _confidence(metric_type: str, metric_name: str, accuracy_labels: dict) -> str:
    """
    返回指标置信度标签。

    参数
    ----
    metric_type : str
    metric_name : str
    accuracy_labels : dict — ACCURACY_LABELS 映射表

    返回
    ----
    str — 'high' / 'medium' / 'low'
    """
    key = f"{metric_type}_{metric_name}"
    return accuracy_labels.get(key, accuracy_labels.get(metric_type, "medium"))


def _build_labels(accuracy_labels: dict, accuracy_label_text: dict) -> dict:
    """
    构建完整的置信度映射字典（供前端展示）。

    参数
    ----
    accuracy_labels : dict — 指标 -> 置信度级别 映射
    accuracy_label_text : dict — 置信度级别 -> 中文文本 映射

    返回
    ----
    dict — {指标名: {level: str, text: str}}
    """
    confidence = {}
    for key, label in accuracy_labels.items():
        confidence[key] = {
            "level": label,
            "text": accuracy_label_text.get(label, label),
        }
    return confidence


def _build_health_score(
    s2, s3, s4_action, s4_verify, flaky_data, cache_data, case_dominance
) -> float:
    """
    计算总健康度评分（加权平均）。
    ★ C-1-1/C-1-2: 8维加权 + 缺维权重重分配（按比例制，总和=1.0）

    维度定义（需求规格 §2.5）：
      Step2 覆盖率         0.15
      Step3 交叉校验       0.15
      Step3 用例质量       0.15
      Step4 守卫拦截       0.10
      Step4 三轨验证       0.10
      Step4 缓存命中       0.10
      用例主导度           0.10
      Flaky 检测           0.10

    缺维重分配：当某维度数据不可用(denominator<=1)时，
    该维度得分置 null，权重按比例重分配给剩余维度。

    返回
    ----
    float — 加权平均得分（四舍五入到整数）
    """
    # ★ C-1-2: 8维比例制权重（总和=1.0）
    _DIM_WEIGHTS = [
        ("step2_coverage", 0.15),
        ("step3_cross_check", 0.15),
        ("step3_quality", 0.15),
        ("step4_guard_intercept", 0.10),
        ("step4_verify", 0.10),
        ("step4_cache_hit", 0.10),
        ("case_dominance", 0.10),
        ("flaky_detection", 0.10),
    ]

    # 收集各维度得分 + 可用性标记
    dim_scores = {}
    dim_available = {}

    # Step 2: 覆盖率
    _v = s2.get("coverage_forward")
    if _v is not None and isinstance(_v, (int, float)):
        dim_scores["step2_coverage"] = _v
        dim_available["step2_coverage"] = True

    # Step 3: 交叉校验 = (100 - 幻觉率)
    _v = s3.get("rate")
    if _v is not None and isinstance(_v, (int, float)):
        dim_scores["step3_cross_check"] = max(0, 100 - _v)
        dim_available["step3_cross_check"] = True

    # ★ C-1-1: Step 3 用例质量（第9维）
    _v = s3.get("avg_quality_score")
    if _v is not None and isinstance(_v, (int, float)):
        dim_scores["step3_quality"] = _v
        dim_available["step3_quality"] = True

    # Step 4 action: (100 - 守卫拦截率)
    _v = s4_action.get("rate")
    if _v is not None and isinstance(_v, (int, float)):
        dim_scores["step4_guard_intercept"] = max(0, 100 - _v)
        dim_available["step4_guard_intercept"] = True

    # Step 4 verify: (100 - 不一致率)
    _v = s4_verify.get("rate")
    if _v is not None and isinstance(_v, (int, float)):
        dim_scores["step4_verify"] = max(0, 100 - _v)
        dim_available["step4_verify"] = True

    # Flaky: (100 - flaky_rate)
    if flaky_data.get("rate") is not None:
        dim_scores["flaky_detection"] = max(0, 100 - flaky_data["rate"])
        dim_available["flaky_detection"] = True

    # 缓存命中率
    if cache_data.get("rate") is not None:
        dim_scores["step4_cache_hit"] = cache_data["rate"]
        dim_available["step4_cache_hit"] = True

    # 用例主导度
    if case_dominance.get("score") is not None:
        dim_scores["case_dominance"] = case_dominance["score"]
        dim_available["case_dominance"] = True

    # ★ C-1-1: 缺维权重重分配
    available_dims = [
        (name, weight) for name, weight in _DIM_WEIGHTS if name in dim_available
    ]
    if not available_dims:
        return 0

    total_orig_weight = sum(w for _, w in available_dims)
    # 重分配：每个可用维度的有效权重 = 原权重 * (总原权重 / 总目标权重)
    # 简化：等比缩放使可用维度权重总和 = 1.0
    scale = 1.0 / total_orig_weight if total_orig_weight > 0 else 1.0

    overall_score = sum(
        dim_scores[name] * weight * scale for name, weight in available_dims
    )
    return round(overall_score)
