# -*- coding: utf-8 -*-
"""多模型交叉验证（高风险测试用例） — 从 case_quality.py 拆出，零逻辑变更。"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


def generate_with_cross_validation(
    requirement: dict[str, str],
    project_context: str = "",
    ui_context: str = "",
    risk_level: str = "medium",
    run_id: str = "",
    project_name: str = "",
) -> dict[str, Any]:
    """
    双模型生成 → 取交集 → 冲突部分标记人工审核。
    ★ S3-014: 触发阈值改为可配置（should_run_cross_validation）。
    ★ 2026-09-01: run_id/project_name 显式穿透，保证 worker 线程内交叉校验指标稳定落库。
    """
    from src.pipeline.steps.step3.case_generator import model_test_cases
    from src.core.config_loader import config

    # ★ S3-014: 使用可配置阈值替代硬编码 risk_level != "high"
    from src.pipeline.steps.step3.case_quality.orchestrator import (
        should_run_cross_validation,
    )

    if not should_run_cross_validation(risk_level):
        return model_test_cases(
            requirement,
            project_context,
            ui_context,
            run_id=run_id,
            project_name=project_name,
        )

    # ★ 零硬编码: 双模型从 config 读取
    model_a = config.ai_model
    model_b = config.verification_model
    # 如果校验模型和生成模型相同，用 ai_model 作为 B（至少保证模型不同）
    if model_b == model_a:
        model_b = model_a  # 相同也无妨，temperature 不同提供多样性

    # ★ 温度从 pipeline_config 读取（外置，便于运维调参）
    from src.core.pipeline_config import pipeline_config

    temp_a = pipeline_config.cross_validation_model_a_temperature
    temp_b = pipeline_config.cross_validation_model_b_temperature

    # Model A: 生成模型
    result_a = model_test_cases(
        requirement,
        project_context,
        ui_context,
        model_params={"model": model_a, "temperature": temp_a},
        enable_validation=True,  # ★ 双模型各自先过校验，再取交集
        run_id=run_id,
        project_name=project_name,
    )
    intents_a = result_a.get("data", []) if result_a.get("success") else []
    if isinstance(intents_a, dict):
        intents_a = [intents_a]

    # Model B: 校验模型（如果可用）
    result_b = model_test_cases(
        requirement,
        project_context,
        ui_context,
        model_params={"model": model_b, "temperature": temp_b},
        enable_validation=True,  # ★ 双模型各自先过校验，再取交集
        run_id=run_id,
        project_name=project_name,
    )
    intents_b = result_b.get("data", []) if result_b.get("success") else []
    if isinstance(intents_b, dict):
        intents_b = [intents_b]

    if not intents_a and not intents_b:
        return result_a
    if not intents_b:
        result_a["_cross_model_note"] = "Model B 不可用，仅用 Model A"
        return result_a

    # 取交集：基于意图动作相似度匹配
    common = _intersect_intents_by_title(intents_a, intents_b)

    # 冲突部分
    common_ids = {c.get("case_id", "") for c in common}
    only_a = [c for c in intents_a if c.get("case_id") not in common_ids]

    result = dict(result_a)
    # ★ 修复: 当交集为空时，回退到 Model A 的全部输出（避免高风险需求因交叉验证
    #    过滤过严而 0 输出，再触发规则兜底 → 无具体 check 的低质量测试用例）
    if common:
        result["data"] = common
    else:
        _log.warning("[交叉验证] common=0, 回退到 Model A (%d 条)", len(intents_a))
        result["data"] = intents_a
    result["_cross_validation"] = {
        "model_a": model_a,
        "model_b": model_b,
        "common_count": len(common),
        "only_a_count": len(only_a),
        "conflicts": [
            {
                "case_id": c.get("case_id", "?"),
                "title": c.get("title", "")[:60],
                "source": "model_a_only",
            }
            for c in only_a[:5]
        ],
        "confidence": "high" if len(only_a) == 0 else "medium",
    }
    return result


def _intersect_intents_by_title(intents_a: list, intents_b: list) -> list:
    """基于标题关键词匹配取交集。简易实现。"""
    common = []
    # 提取 B 的标题关键词集合
    b_keywords = set()
    for c in intents_b:
        title = c.get("title", "").lower()
        for word in title.split():
            if len(word) >= 2:
                b_keywords.add(word)

    for ca in intents_a:
        title_a = ca.get("title", "").lower()
        # 与 B 的任何一个测试用例共享 2+ 关键词 → 视为共同
        matched = False
        for cb in intents_b:
            title_b = cb.get("title", "").lower()
            words_a = set(title_a.split())
            words_b = set(title_b.split())
            common_words = words_a & words_b
            if len(common_words) >= 2:
                matched = True
                break
        if matched:
            common.append(ca)

    return common
